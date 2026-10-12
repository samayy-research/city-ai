"""Demo continuity and email preparation without database or browser persistence."""
import json
import os
import unittest
from unittest.mock import patch

import app
import coordination
import demo_session
import project_store
from project_workflow import empty_project,update_project,build_brief


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.environment=patch.dict(os.environ,{"PROJECT_STORAGE_MODE":"demo","VERCEL":"1","UPSTASH_REDIS_REST_URL":"","UPSTASH_REDIS_REST_TOKEN":"","KV_REST_API_URL":"","KV_REST_API_TOKEN":""})
        self.environment.start()
        self.key=patch.object(app,'OPENAI_API_KEY','test-demo-secret');self.key.start()

    def tearDown(self):
        self.key.stop();self.environment.stop()

    def result(self,project):
        return {"project":project,"message":"What cooking is proposed?","stage":"follow_up","brief":None,"citations":[]}

    def test_vercel_demo_needs_no_redis_and_performs_no_project_writes(self):
        with patch.object(project_store,'save') as save,patch.object(project_store,'load') as load,patch.object(app,'openai_chat',return_value=self.result(empty_project())):
            result=app.chat_request({'message':'A restaurant'})
        self.assertEqual(result['mode'],'demo')
        self.assertNotIn('projectId',result)
        self.assertTrue(result['state'])
        save.assert_not_called();load.assert_not_called()
        self.assertTrue(app.deployment_readiness()['ready'])

    def test_signed_context_preserves_scope_across_requests(self):
        project=update_project(empty_project(),{'existing_use':{'value':'office','evidence':'office'}},'office')
        with patch.object(app,'openai_chat',return_value=self.result(project)):
            first=app.chat_request({'message':'office'})
        with patch.object(app,'openai_chat',return_value=self.result(project)) as model:
            app.chat_request({'state':first['state'],'message':'Add rooftop seating'})
        self.assertEqual(model.call_args.args[1]['fields']['existing_use']['value'],'office')
        self.assertEqual(len(model.call_args.args[0]),3)

    def test_tampered_or_wrong_key_context_is_rejected(self):
        state=demo_session.pack({'project':empty_project(),'history':[]},'test-demo-secret')
        with self.assertRaises(ValueError):demo_session.unpack(state+'x','test-demo-secret')
        with self.assertRaises(ValueError):demo_session.unpack(state,'another-secret')

    def test_demo_storage_calls_are_guarded(self):
        with self.assertRaises(project_store.StorageError):project_store.save(project_store.new_id(),{})

    def test_email_draft_uses_signed_tab_context_without_database(self):
        project=update_project(empty_project(),{'business_use':{'value':'yes','evidence':'new business'},'project_description':{'value':'open a restaurant','evidence':'new business'}},'new business')
        state=demo_session.pack({'project':project,'history':[]},'test-demo-secret')
        with patch.object(project_store,'load') as load,patch.object(coordination,'verify_turnstile',return_value=True):
            draft=coordination.prepare_coordination({'state':state,'name':'Applicant','email':'applicant@example.com','team':'Zoning / Certificate of Use (COU)','turnstileToken':'test'},'127.0.0.1')
        self.assertIn('restaurant',draft['body']);load.assert_not_called()

    def test_property_lookup_does_not_cache_applicant_address_in_demo(self):
        project=update_project(empty_project(),{'location':{'value':'701 San Marco','evidence':'701 San Marco'}},'701 San Marco')
        extraction={'updates':{}}
        reply={'message':'The restaurant scope needs confirmation.','question':'What work is proposed?','ready_for_brief':False,'source_ids':[],'fee_id':''}
        with patch.object(app,'complete_json',side_effect=[extraction,reply]),patch.object(app,'research_context',return_value={'sources':[],'fees':[],'property':{}}) as research:
            app.openai_chat([{'role':'user','text':'A restaurant'}],project)
        self.assertFalse(research.call_args.kwargs['cache_property'])


if __name__=='__main__':unittest.main()
