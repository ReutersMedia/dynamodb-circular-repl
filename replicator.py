from __future__ import print_function

import os
import re
import time

import concurrent.futures

import boto3

import logging
LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

REPLICA_SOURCE_REGION_F = '_repl_source_region'
REPLICA_SOURCE_TABLE_F = '_repl_source_table'

TABLE_STREAM_ARN_REGEX = re.compile("^arn\:aws\:dynamodb\:(.*?)\:.*?\:table\/(.*?)\/stream")

class ReplicatorException(Exception):
    pass

def build_dyn_request_iter(recs):
    for r in recs[::-1]:
        op = r['eventName'] # INSERT, MODIFY, REMOVE
        dyn = r['dynamodb']
        if op=='REMOVE':
            # always replicate deletes
            # a bit dangerous, since an insert followed by a delete may wind up having a delete come back round
            # and kill the new item
            # for a safe delete, suggest check to see if objects match before deleting, but not doing that
            yield (dyn['Keys'],{'DeleteRequest':{'Key':dyn['Keys']}})
        else:
            repl_region = dyn['NewImage'].get(REPLICA_SOURCE_REGION_F)
            repl_table = dyn['NewImage'].get(REPLICA_SOURCE_TABLE_F)
            if repl_region is not None and repl_region==os.getenv('TARGET_REGION') \
               and repl_table is not None and repl_table==os.getenv('TARGET_TABLE'):
                # skip, do not replicate back to the source
                continue
            # record the source of the record
            m = TABLE_STREAM_ARN_REGEX.match(r['eventSourceARN'])
            if m==None:
                raise ReplicatorException("Unable to parse table and region from ARN: {0}".format(r['eventSourceARN']))
            new_item = dyn['NewImage'].copy()
            if REPLICA_SOURCE_REGION_F not in new_item:
                new_item[REPLICA_SOURCE_REGION_F] = {'S':m.groups()[0]}
                new_item[REPLICA_SOURCE_TABLE_F] = {'S':m.groups()[1]}
            yield (dyn['Keys'],{'PutRequest':{'Item':new_item}})


def write_dyn_batch(b):
    request_list = [x[1] for x in b]
    try:
        session = boto3.session.Session(region_name=os.getenv('TARGET_REGION'))
        c = session.client('dynamodb')
        r = c.batch_write_item(RequestItems={os.getenv('TARGET_TABLE'):request_list})
        unproc = r.get('UnprocessedItems')
        if unproc is not None and len(unproc)>0:
            # need to rebuild the original key/request structure
            # for splitting unprocessed items into batches
            return [x for x in b if x[1] in unproc]
        return []
    except:
        LOGGER.exception("Error inserting batch")
        # assume all failed
        return b

def split_recs_into_batches(recs):
    # split when hit a key that already exists, or 25 elements
    # dynamo will error if same key in same write batch
    # but should always preserve order
    cur_batch = []
    for key,req in recs:
        if len(cur_batch)>=25 or any([x for x in cur_batch if x[0]==key]):  # no sets or 'in', key is a dict
            yield(cur_batch)
            # new batch
            cur_batch = []
        cur_batch.append((key,req))
    yield cur_batch
        
def k_seq(x):
    return x['dynamodb'].get('SequenceNumber',0)

def lambda_handler(event, context):
    recs = [x for x in event['Records'] if 'dynamodb' in x]
    recs.sort(key=lambda x: x['dynamodb']['SequenceNumber'])
    dyn_requests = build_dyn_request_iter(recs)
    tstart = time.time()
    try_cnt = 0
    while time.time()-tstart < 245:
        batches = split_recs_into_batches(dyn_requests)
        failures = []
        try_cnt += 1
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_batch = {executor.submit(write_dyn_batch, b): b for b in batches}
            for future in concurrent.futures.as_completed(future_to_batch):
                batch = future_to_batch[future]
                failures.extend(future.result())
        if len(failures)==0:
            break
        LOGGER.info("Failure sending dynamo write batch, waiting and retrying (attempt {0})".format(try_cnt))
        time.sleep(max(5*try_cnt,245-time.time()+tstart))
        dyn_requests = failures
    if len(failures)!=0:
        # we've failed!
        raise ReplicatorException("Unable to handle {0} out of {1} requests".format(len(failures),len(recs)))
    else:
        LOGGER.info("Handled {0} records in {1} sec".format(len(recs),time.time()-tstart))
    
            
