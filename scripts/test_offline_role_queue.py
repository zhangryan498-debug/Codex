from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import unittest
from offline_role_queue import Queue,ROLES,ValidationError,IdentityConflict,DependencyError,LeaseError

T='2026-11-11T17:00:00+00:00'
LATER='2026-11-11T17:00:10+00:00'
def job(name='J',**kw):return dict(job_id=name,idempotency_key='key-'+name,platform='douyin',country='CN',role_suffix='lead',kind='offline_review',task='Synthetic offline task',**kw)


class QueueTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.db=Path(self.temp.name)/'queue.sqlite';self.q=Queue(self.db)
    def tearDown(self):self.temp.cleanup()
    def test_twenty_routes_are_explicit_and_platform_is_not_guessed(self):
        for platform,country in [('douyin','CN'),('tiktok','PH')]:
            jobs=[]
            for role in ROLES:
                p=job(platform+'-'+role);p.update(platform=platform,country=country,role_suffix=role);jobs.append(p)
            self.q.submit(jobs)
        self.assertEqual({r['role'] for r in self.q.list_jobs()},{p+'-'+r for p in ('douyin','tiktok') for r in ROLES})
    def test_bad_routes_and_settings_are_rejected_without_mutation(self):
        for patch in [{'country':'ID'},{'platform':False},{'role_suffix':['lead']},{'role_suffix':'unknown'},{'model':'other'},{'priority':True}]:
            p=job();p.update(patch)
            with self.assertRaises(ValidationError):self.q.submit([p])
        self.assertEqual(self.q.list_jobs(),[])
    def test_duplicate_is_idempotent_and_changed_payload_conflicts(self):
        self.q.submit([job()]);self.q.submit([job()]);self.assertEqual(len(self.q.list_jobs()),1)
        for patch in [{'task':'changed'},{'idempotency_key':'new-key'},{'job_id':'other-id'}]:
            p=job();p.update(patch)
            with self.assertRaises(IdentityConflict):self.q.submit([p])
        self.assertEqual(self.q.get('J')['attempts'],0)
    def test_idempotency_key_is_opaque_and_not_a_filesystem_path(self):
        p=job();p['idempotency_key']='批次:stock/review:alpha';self.q.submit([p]);self.q.submit([p]);self.assertEqual(len(self.q.list_jobs()),1)
    def test_same_batch_duplicate_is_idempotent_but_conflicting_copy_is_atomic(self):
        self.q.submit([job(),job()]);self.assertEqual(len(self.q.list_jobs()),1)
        p=job('K');changed=deepcopy(p);changed['task']='different'
        with self.assertRaises(IdentityConflict):self.q.submit([p,changed])
        self.assertEqual(len(self.q.list_jobs()),1)
    def test_atomic_batch_rolls_back_and_cycles_cannot_enter(self):
        for batch in [[job('A'),job('B',requires=['missing'])],[job('A',requires=['B']),job('B',requires=['A'])]]:
            with self.assertRaises(DependencyError):self.q.submit(batch)
            self.assertEqual(self.q.list_jobs(),[])
    def test_priority_and_tie_break_are_deterministic(self):
        self.q.submit([job('B',priority=50),job('A',priority=50),job('C',priority=70)])
        self.assertEqual([self.q.claim('w',T,30)['job_id'] for _ in range(3)],['C','A','B'])
    def test_parallel_claims_only_one_model_attempt_can_start(self):
        self.q.submit([job()]);barrier=threading.Barrier(2)
        def f(w):
            q=Queue(self.db);barrier.wait();return q.claim(w,T,30)
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(f,['w1','w2']))
        self.assertEqual(sum(r is not None for r in results),1)
        winner=next(r for r in results if r);self.q.start('J',winner['token'],T)
        self.assertEqual(self.q.get('J')['attempts'],1)
    def test_parallel_identical_submissions_create_one_row(self):
        barrier=threading.Barrier(2)
        def f(_):q=Queue(self.db);barrier.wait();return q.submit([job()])
        with ThreadPoolExecutor(2) as pool:list(pool.map(f,range(2)))
        self.assertEqual(len(self.q.list_jobs()),1)
    def test_lease_expiry_before_start_can_be_reclaimed(self):
        self.q.submit([job()]);one=self.q.claim('one',T,5);two=self.q.claim('two',LATER,5)
        self.assertNotEqual(one['token'],two['token'])
        with self.assertRaises(LeaseError):self.q.start('J',one['token'],LATER)
    def test_running_expiry_is_uncertain_and_never_auto_retried(self):
        self.q.submit([job()]);one=self.q.claim('one',T,5);attempt=self.q.start('J',one['token'],T);self.q.reap(LATER)
        self.assertEqual(self.q.get('J')['state'],'uncertain');self.assertIsNone(self.q.claim('two',LATER,5))
        with self.assertRaises(LeaseError):self.q.settle('J',one['token'],'completed',{},LATER)
        self.q.resolve('J',attempt,'completed',{'kind':'engineering-only-mock'})
        self.assertEqual(self.q.get('J')['attempts'],1)
    def test_wrong_token_and_wrong_attempt_do_not_change_state(self):
        self.q.submit([job()]);claim=self.q.claim('one',T,30)
        with self.assertRaises(LeaseError):self.q.start('J','wrong',T)
        attempt=self.q.start('J',claim['token'],T)
        with self.assertRaises(LeaseError):self.q.settle('J','wrong','completed',{},T)
        self.q.abandon('J',claim['token'],'engineering-only mock crash',T)
        with self.assertRaises(LeaseError):self.q.resolve('J','wrong','completed',{})
        self.assertEqual(self.q.get('J')['state'],'uncertain')
    def test_dependency_order_and_failed_terminal_do_not_auto_retry(self):
        self.q.submit([job('A'),job('B',requires=['A'])]);claim=self.q.claim('w',T,30);self.q.start('A',claim['token'],T)
        self.assertIsNone(self.q.claim('other',T,30));self.q.settle('A',claim['token'],'failed',{'mock':True},T)
        self.assertIsNone(self.q.claim('other',T,30));self.assertEqual(self.q.get('B')['attempts'],0)
    def test_price_review_requires_explicit_dedicated_lane(self):
        p=job();p.update(kind='price_review')
        with self.assertRaises(ValidationError):self.q.submit([p])
        p.update(role_suffix='shop-ops',price_input='caller.json',price_input_sha256='a'*64,evaluation_time=T)
        self.q.submit([p]);self.assertEqual(self.q.get('J')['role'],'douyin-shop-ops')
    def test_price_dependencies_require_correct_route_and_current_revalidator(self):
        p=job('P');p.update(role_suffix='shop-ops',kind='price_review',price_input='caller.json',price_input_sha256='a'*64,evaluation_time=T)
        self.q.submit([p,job('D',requires=['P'],requires_price=['P'],evaluation_time=T)])
        claim=self.q.claim('w',T,30);self.q.start('P',claim['token'],T);self.q.settle('P',claim['token'],'completed',{'model_says_accepted':True},T)
        self.assertIsNone(self.q.claim('w',T,30));self.assertEqual(self.q.get('D')['state'],'blocked')
    def test_preflight_block_does_not_start_a_model_attempt(self):
        self.q.submit([job()]);claim=self.q.claim('w',T,30);self.q.block_leased('J',claim['token'],'Missing caller',T)
        self.assertEqual(self.q.get('J')['attempts'],0);self.assertFalse(self.q.get('J')['evidence']['model_called'])
    def test_one_uncertain_attempt_does_not_expire_other_workers(self):
        self.q.submit([job('A'),job('B')]);a=self.q.claim('w1',T,30);b=self.q.claim('w2',T,30)
        self.q.start('A',a['token'],T);self.q.start('B',b['token'],T);self.q.abandon('A',a['token'],'mock uncertain outcome',T)
        self.assertEqual(self.q.get('B')['state'],'running')
    def test_time_and_lease_types_are_strict(self):
        self.q.submit([job()])
        for ttl in [True,0,-1,1.5,'5',3601]:
            with self.assertRaises(ValidationError):self.q.claim('w',T,ttl)
        with self.assertRaises(ValidationError):self.q.claim('w','2026-11-11T00:00:00',30)

if __name__=='__main__':unittest.main()
