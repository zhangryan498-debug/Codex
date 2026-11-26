"""Drain a finite number of ready offline jobs. No daemon or scheduled service."""
import argparse
import json
from offline_queue_worker import Worker


def drain(worker,limit):
    if type(limit) is not int or not 1<=limit<=100:raise ValueError('A finite task limit 1..100 is required')
    completed=[]
    for _ in range(limit):
        result=worker.run_once()
        if result is None:break
        completed.append({'job_id':result['job_id'],'state':result['state']})
    return {'scope':'synthetic_offline','release_status':'not_authorized','processed':completed,'max_jobs':limit,'states':{row['job_id']:row['state'] for row in worker.queue.list_jobs()},'automatic_retry':False}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--project',required=True);p.add_argument('--db',required=True);p.add_argument('--artifacts',required=True);p.add_argument('--limit',type=int,default=20);a=p.parse_args()
    print(json.dumps(drain(Worker(a.project,a.db,a.artifacts),a.limit),ensure_ascii=False))
