## DynamoDB Circular Replication

This is a simple pattern for DynamoDB Circular (Multi-master) replication, using Serverless (http://www.serverless.com) and Lambdas.

The stack is deployed against each of the tables that needs to be replicated.  In the example below, replication is set up between two tables.  The tables need to have the same Primary Key, and have a stream specification with view type NEW_AND_OLD_IMAGES.

When a change is made to one of the tables (Table A), the Lambda inspects the stream event and writes or deletes the item from it's target table (Table B).  The region and replication origin fields is set in two fields for the replicated entry: _repl_source_region and _repl_source_table.  When this entry is inserted into the target Table B, another event will be emitted on the target table's Kinesis stream.  Before inserting an item into a table, the _repl_* fields are inspected to determine if the table is the original table (Table A).  If it is, an insert is not made.  In this way, the item can be replicated along a circle of Tables.

Deletes are always replicated, and because the Lambda is not able to add the _repl_* tags, there is no way to track the origin.  However if the replication process transits the circle of Tables before another item is added to the source with the same key, then replication of the delete will propagate fully and stop at the source.  This replication pattern therefore does not work if objects are deleted and then re-inserted with the same Key faster than replication occurs.  Note that replication might lag for some reason.  I therefore only recommend the solution for insert and update traffic only, and delete traffic only if deletes are permanent.  This could be solved at some point with a separate table for tracking deletes.  The tables could also become inconsistent, if two items with the same Key are inserted at about the same time in multiple tables.  In which case two versions may exist.

In the event of failure, and the Lambda is unable to replicate any of the items, it will throw an exception and cause the Kinesis data records to be retained and retried.  This retry process will last up to the stream retention period, by default 24 hours.  The Lambda will only process records in order, and will halt until the records are processed.



## Deployment

The serverless stack takes three arguments:

 * --stream-arn: the ARN of the event stream for the source table
 * --region: the region for the Lambda replication function, which must match the region of the stream-arn
 * --target-region: the region for the target DynamoDB table
 * --target-table: the name of the target DynamoDB table

An example for two tables:

```
$ serverless deploy --env test1 --region us-east-1 \
     --source-stream "arn:aws:dynamodb:us-east-1:XXXXX:table/test-replicator/stream/2017-04-06T17:06:14.353" \
     --target-region "eu-west-1" \
     --target-table "test-replicator"

$ serverless deploy --env test1 --region eu-west-1 \
     --source-stream "arn:aws:dynamodb:eu-west-1-1:XXXXX:table/test-replicator/stream/2017-04-06T17:43:52.751" \
     --target-region "us-east-1" \
     --target-table "test-replicator"
```

## Testing

To test, run the scripts in ./test:

```
cd ./test
./setup
./run_test
./cleanup