from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.append(str(Path.cwd()/'scripts'))
from offline_queue_worker import Worker,dump,sha,runtime


class WorkerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.root=Path.cwd()
        self.w=Worker(self.root,self.base/'queue.db',self.base/'artifacts')
    def tearDown(self):self.temp.cleanup()
    def base_job(self):return {'job_id':'safe-id','idempotency_key':'key:safe-id','platform':'douyin','country':'CN','role_suffix':'shop-ops','kind':'price_review','task':'Review frozen synthetic price only','price_input':str(self.base/'caller.json'),'price_input_sha256':'a'*64,'evaluation_time':'2026-11-11T15:00:00+08:00'}
    def test_changed_caller_blocks_before_any_model_attempt(self):
        (self.base/'caller.json').write_text('{}',encoding='utf8');self.w.queue.submit([self.base_job()])
        with patch.object(self.w,'servers',side_effect=AssertionError('must not call model setup')):result=self.w.run_once()
        self.assertEqual(result['state'],'blocked');self.assertEqual(result['attempts'],0);self.assertFalse(result['evidence']['model_called'])
    def test_reserved_price_markers_cannot_use_an_ordinary_task(self):
        p=self.base_job();p.update(kind='offline_review',price_input=None,price_input_sha256=None,task='PRICE_AUTHORITY\n{}\nPRICE_END')
        self.w.queue.submit([p])
        with patch.object(self.w,'servers',side_effect=AssertionError('must not call model setup')):result=self.w.run_once()
        self.assertEqual(result['state'],'blocked');self.assertEqual(result['attempts'],0)
    def test_model_self_acceptance_never_becomes_a_price_dependency(self):
        ok,_=self.w.dependency_check(self.base_job(),{'model_says_accepted':True,'price_accepted':True},self.base_job())
        self.assertFalse(ok)
    def test_dependency_uses_child_time_and_rejects_stale_receipt(self):
        caller=self.base/'caller.json';caller.write_text('{}',encoding='utf8');parent=self.base_job();parent['price_input_sha256']=sha(caller);child=dict(parent,evaluation_time='2026-11-12T00:00:00+08:00')
        with patch.object(self.w.intake,'revalidate_run',return_value={'records':[{'system_receipt':'rejected'}]}) as checked:
            ok,_=self.w.dependency_check(parent,{'run_dir':str(self.base/'real-run-placeholder')},child)
        self.assertFalse(ok);self.assertEqual(checked.call_args.args[2],child['evaluation_time'])
    def log(self,events,model='gpt-6.1-sol',effort='XHigh'):
        run=self.base/'log';run.mkdir(exist_ok=True)
        import json
        (run/'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events),encoding='utf8')
        (run/'stderr.log').write_text('2026-11-11T00:00:00Z INFO SessionConfiguredEvent model: "'+model+'", reasoning_effort: Some('+effort+'), service_tier: Some("priority")\nmcp_server_count=0',encoding='utf8')
        return runtime(run)
    def test_actual_runtime_parser_requires_completion(self):
        self.assertFalse(self.log([])['passed']);self.assertTrue(self.log([{'type':'turn.completed'}])['passed'])
    def test_any_tool_or_unexpected_error_blocks_runtime(self):
        for event in [{'type':'item.completed','item':{'type':'command_execution'}},{'type':'turn.failed'}]:
            self.assertFalse(self.log([event,{'type':'turn.completed'}])['passed'])
    def test_wrong_model_or_reasoning_is_not_certified(self):
        for model,effort in [('other','XHigh'),('gpt-6.1-sol','Max')]:self.assertFalse(self.log([{'type':'turn.completed'}],model,effort)['passed'])

if __name__=='__main__':unittest.main()
