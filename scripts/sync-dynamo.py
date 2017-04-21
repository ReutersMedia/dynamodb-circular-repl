import boto3
import sys
import time
from boto3.dynamodb.conditions import Key, Attr

"""
Synchronize everything from the primary to the master, by setting an arbitrary value for each 
item in the primary:

_sync=int(time.time())

Then the secondary is inspected for all documents, and any items without the _sync value are
also updated.
"""


if len(sys.argv) != 4:
    print "Usage: {0} table primary-region secondary-region".format(sys.argv[0])
    sys.exit(1)
tname = sys.argv[1]
primary = sys.argv[2]
secondary = sys.argv[3]

r1 = boto3.resource('dynamodb',region_name=primary)
r2 = boto3.resource('dynamodb',region_name=secondary)

t1 = r1.Table(tname)
t2 = r2.Table(tname)


def collect_results(table_f,qp):
    items = []
    while True:
        r = table_f(**qp)
        items.extend(r['Items'])
        lek = r.get('LastEvaluatedKey')
        if lek is None or lek=='':
            break
        qp['ExclusiveStartKey'] = lek
    return items

keys = ', '.join([x['AttributeName'] for x in t1.key_schema])

t1_items = collect_results(t1.scan,
                           {"Select":"SPECIFIC_ATTRIBUTES",
                            "ProjectionExpression":keys})

tnow = int(time.time())
for item in t1_items:
    print "updating {0!r}".format(item)
    t1.update_item(Key=item,
                   UpdateExpression='set #s = :x',
                   ExpressionAttributeNames = {'#s':'_sync'},
                   ExpressionAttributeValues = {':x':tnow})
print "pausing for sync to complete"
time.sleep(20)
t2_items = collect_results(t2.scan,
                           {"Select":"SPECIFIC_ATTRIBUTES",
                            "ProjectionExpression":keys,
                            "FilterExpression":Attr('_sync').ne(tnow)})

tnow = int(time.time())
for item in t2_items:
    print "updating {0!r}".format(item)
    t2.update_item(Key=item,
                   UpdateExpression='set #s = :x',
                   ExpressionAttributeNames = {'#s':'_sync'},
                   ExpressionAttributeValues = {':x':tnow})
                   
