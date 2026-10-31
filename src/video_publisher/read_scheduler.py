"""Bounded platform concurrency with independent durable journals and one merge lock."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from copy import deepcopy
from threading import RLock
import json
import sys
import time
import uuid


MERGE_LOCK = RLock()


def execute(run, progress=False):
    from . import operations_runs as runs, operations_api
    started = time.monotonic()
    children = run.setdefault('children', [])
    if not children:
        for platform in dict.fromkeys(j['platform'] for j in run['jobs']):
            jobs = [deepcopy(j) for j in run['jobs'] if j['platform'] == platform]
            child = {**deepcopy(run), 'run_id': uuid.uuid4().hex, 'parent_run_id': run['run_id'],
                     'jobs': jobs, 'events': [], 'batches': [], 'browser_sessions': 0,
                     'options': {**run['options'], 'workers': 1}}
            child.pop('children', None)
            runs.save(child)
            children.append({'platform': platform, 'run_id': child['run_id']})
        runs.save(run)  # Dispatch only after all child IDs are recoverable.

    def collect():
        snapshots = [runs.status(c['run_id']) for c in children]
        by_id = {j['job_id']: j for c in snapshots for j in c['jobs']}
        run['jobs'] = [by_id[j['job_id']] for j in run['jobs']]
        run['events'] = [e for c in snapshots for e in c['events']]
        run['browser_sessions'] = sum(c['browser_sessions'] for c in snapshots)
        return snapshots

    def work(child):
        with operations_api.read_session():
            try:
                return runs.execute_serial(child)
            except BaseException:
                child['state'] = 'INTERRUPTED'
                runs.save(child)
                raise

    snapshots = collect()
    completed_before = sum(j['state'] == 'DONE' for j in run['jobs'])
    last = None
    with ThreadPoolExecutor(max_workers=run['options']['workers'], thread_name_prefix='vpub-read') as pool:
        pending = {pool.submit(work, c) for c in snapshots if c['state'] != 'COLLECTED'}
        while pending:
            ready, pending = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
            collect()
            signature = tuple((j['job_id'], j['state']) for j in run['jobs'])
            elapsed = round(time.monotonic() - started, 3)
            done = sum(j['state'] == 'DONE' for j in run['jobs'])
            if done > completed_before:
                run.setdefault('first_result_seconds', elapsed)
            if signature != last:
                runs.save(run)
                if progress:
                    print(json.dumps({'event': 'read_progress', 'run_id': run['run_id'],
                          'elapsed_seconds': elapsed, 'done': done,
                          'cached': sum(j['state']=='CACHED' for j in run['jobs']),
                          'total': len(run['jobs']), 'platform_writes': 0}), file=sys.stderr, flush=True)
                last = signature
            for future in ready:
                future.result()
    collect()
    run['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return runs.finalize(run)
