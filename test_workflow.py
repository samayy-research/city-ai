"""Regression scenarios for project memory, dependencies, exceptions and email purpose.

These are engineering expectations, not City-approved permitting determinations.
"""
import json
import os
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import app
import coordination
import project_store
from project_workflow import build_brief, email_draft, empty_project, update_project


def project(values):
    statement = " | ".join(value for value in values.values())
    return update_project(empty_project(), {key: {"value": value, "evidence": value} for key, value in values.items()}, statement)


class WorkflowTests(unittest.TestCase):
    def test_address_policy_follows_a_project_description(self):
        record=project({"project_description":"convert an office to a restaurant"})
        result=app.apply_question_policy({"message":"I can help.","question":"What work is planned?","ready_for_brief":False},record)
        self.assertEqual(result["question_field"],"location")
        self.assertIn("address",result["question"].lower())

    def test_historic_scope_policy_asks_about_exterior_work(self):
        record=project({"project_description":"renovate a historic district storefront", "location":"123 Main St", "historic_review":"yes"})
        result=app.apply_question_policy({"message":"I can help.","question":"What work is planned?","ready_for_brief":False},record)
        self.assertEqual(result["question_field"],"exterior_work")
        self.assertIn("outside",result["question"].lower())

    def test_historic_route_is_omitted_for_reported_interior_only_work(self):
        record=project({"historic_review":"yes", "exterior_work":"no"})
        steps={step["id"]:step for step in build_brief(record)["action_plan"]}
        self.assertEqual(steps["historic"]["status"],"not_applicable")

    def test_use_conversion_preserves_scope_and_orders_use_before_building(self):
        record = project({"existing_use": "office", "proposed_use": "restaurant", "project_description": "convert an office to a restaurant",
                          "business_use": "yes", "interior_work": "yes", "plumbing": "yes", "specialty_work": "commercial cooking"})
        brief = build_brief(record)
        steps = {step["id"]: step for step in brief["action_plan"]}
        self.assertIn("office", brief["project_summary"])
        self.assertEqual(brief["city_conversations"][0]["group"], "Zoning / Certificate of Use (COU)")
        self.assertEqual(steps["building"]["prerequisites"], ["use"])
        self.assertEqual(steps["occupancy"]["status"], "unresolved")

    def test_unknown_business_use_is_visible_and_blocks_submission_coordination(self):
        brief = build_brief(project({"interior_work": "yes", "project_description": "alter the interior"}))
        steps = {step["id"]: step for step in brief["action_plan"]}
        self.assertEqual(steps["use"]["status"], "unresolved")
        self.assertEqual(steps["building"]["prerequisites"], ["use"])

    def test_unknown_does_not_erase_state_or_accept_invented_evidence(self):
        record = project({"existing_use": "office", "site_work": "no"})
        updated = update_project(record, {"existing_use": {"value":"unknown", "evidence":"I don't know"},
                                         "site_work": {"value":"yes", "evidence":"new driveway"}}, "I don't know")
        self.assertEqual(updated["fields"]["existing_use"]["value"], "office")
        self.assertEqual(updated["fields"]["site_work"]["value"], "no")

    def test_unclear_conflict_remains_unresolved_until_explicit_correction(self):
        record = project({"site_work":"no"})
        conflicted = update_project(record, {"site_work":{"value":"yes", "evidence":"new driveway"}}, "new driveway")
        self.assertIn("site_work", conflicted["conflicts"])
        self.assertEqual(next(step for step in build_brief(conflicted)["action_plan"] if step["id"] == "site")["status"], "unresolved")
        fixed = update_project(conflicted, {"site_work":{"value":"yes", "evidence":"Actually, add a driveway", "correction":True}}, "Actually, add a driveway")
        self.assertFalse(fixed["conflicts"])
        self.assertEqual(fixed["fields"]["site_work"]["value"], "yes")
        self.assertEqual(fixed["corrections"][0]["previous"], "no")
        self.assertEqual(fixed["fields"]["site_work"]["evidence"], ["Actually, add a driveway"])

    def test_specialty_review_independent_of_interior_work(self):
        brief = build_brief(project({"interior_work":"no", "specialized_review":"yes", "specialty_work":"replace the HVAC"}))
        specialty = next(step for step in brief["action_plan"] if step["id"] == "specialty")
        self.assertEqual(specialty["status"], "applicable")
        self.assertNotIn("Fire Prevention", specialty["department"])
        self.assertFalse(specialty["prerequisites"])

    def test_city_exceptions_and_non_city_row_are_not_auto_permits(self):
        brief = build_brief(project({"site_work":"yes", "site_activity":"parking_restriping_only", "right_of_way":"yes", "right_of_way_authority":"fdot"}))
        steps = {step["id"]: step for step in brief["action_plan"]}
        self.assertEqual(steps["site"]["status"], "not_applicable")
        self.assertEqual(steps["row"]["status"], "unresolved")
        self.assertIn("jurisdiction", steps["row"]["exception_note"])

    def test_department_email_is_short_specific_and_has_one_request(self):
        record = project({"existing_use":"office", "proposed_use":"restaurant", "project_description":"convert an office to a restaurant",
                          "business_use":"yes", "interior_work":"yes", "site_work":"yes", "site_changes":"a new driveway", "alterations":"interior partitions"})
        draft = email_draft(record, build_brief(record), "Zoning / Certificate of Use (COU)", "Applicant", "applicant@example.com")
        self.assertIn("office", draft["body"])
        self.assertIn("Jax Project Advisor", draft["body"])
        self.assertNotIn("driveway", draft["body"])
        self.assertNotIn("checklist", draft["body"].lower())
        self.assertEqual(draft["body"].count("?"), 1)
        self.assertLess(len(draft["body"].split()), 180)

    def test_all_steps_have_source_and_rule_version(self):
        for step in build_brief(empty_project())["action_plan"]:
            self.assertTrue(step["source_url"].startswith("https://www.jacksonville.gov/"))
            self.assertTrue(step["outcome"])
            self.assertTrue(step["rule_version"])


class StateAndApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"PROJECT_DB_PATH":os.path.join(self.temp.name, "projects.sqlite3"),
                                                  "PROJECT_STORAGE_MODE":"persistent",
                                                  "VERCEL":"", "UPSTASH_REDIS_REST_URL":"", "UPSTASH_REDIS_REST_TOKEN":"",
                                                  "KV_REST_API_URL":"", "KV_REST_API_TOKEN":"", "CITY_COORDINATION_EMAIL":"city@example.com"})
        self.environment.start()
        self.model_configuration=patch.object(app,"OPENAI_API_KEY","test")
        self.model_configuration.start()

    def tearDown(self):
        self.model_configuration.stop()
        self.environment.stop()
        self.temp.cleanup()

    def test_durable_record_and_stale_write_protection(self):
        identifier = project_store.new_id()
        record = {"project":project({"existing_use":"office"}), "history":[], "result":None}
        project_store.save(identifier, record)
        self.assertEqual(project_store.load(identifier), record)
        with self.assertRaises(ValueError):
            project_store.save(identifier, record, -1)

    def test_chat_persists_resumes_and_supplies_old_scope_to_model(self):
        record = project({"existing_use":"office", "project_description":"convert an office to a restaurant"})
        response = {"project":record, "message":"Will the work include a new kitchen?", "facts":{}, "stage":"follow_up", "brief":None}
        with patch.object(app, "openai_chat", return_value=response):
            first = app.chat_request({"message":"convert an office to a restaurant"})
        resumed = app.chat_request({"projectId":first["projectId"], "resume":True})
        self.assertEqual(len(resumed["history"]), 2)
        with patch.object(app, "openai_chat", return_value=response) as model:
            app.chat_request({"projectId":first["projectId"], "message":"Yes, a kitchen", "project":{"existing_use":"fabrication"}})
            self.assertEqual(model.call_args.args[1]["fields"]["existing_use"]["value"], "office")

    def test_email_uses_saved_plan_and_ignores_client_recipient_and_checklist(self):
        identifier=project_store.new_id()
        saved=project({"business_use":"yes", "project_description":"convert an office to a restaurant", "proposed_use":"restaurant"})
        project_store.save(identifier, {"project":saved})
        with patch.object(coordination, "verify_turnstile", return_value=True):
            result=coordination.prepare_coordination({"projectId":identifier, "name":"Applicant", "email":"applicant@example.com", "team":"Zoning / Certificate of Use (COU)",
                "turnstileToken":"test", "recipient":"unrelated@coj.net", "checklist":"unrelated confidential dump", "route":"invented"}, "127.0.0.1")
        self.assertTrue(result["mailtoUrl"].startswith("mailto:Zoning%40coj.net?"))
        self.assertNotIn("unrelated", result["body"])
        self.assertIn("restaurant", result["body"])

    def test_local_server_serves_js_and_coordination_endpoint(self):
        class QuietHandler(app.Handler):
            def log_message(self, *args): pass
        server=ThreadingHTTPServer(("127.0.0.1",0),QuietHandler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f"http://127.0.0.1:{server.server_port}"
        try:
            with urllib.request.urlopen(base + "/advisor.js") as result:
                self.assertIn(b"renderBrief",result.read())
            with patch.object(coordination, "prepare_coordination", return_value={"subject":"A short request", "body":"Project summary", "mailtoUrl":"mailto:city@example.com"}):
                request=urllib.request.Request(base+"/api/coordination-request",data=b"{}",headers={"Content-Type":"application/json"})
                with urllib.request.urlopen(request) as result:
                    self.assertEqual(json.load(result)["subject"], "A short request")
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_provider_contract_saves_evidence_without_inventing_occupancy(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps({"choices":[{"message":{"content":json.dumps({"message":"Thanks — I can prepare the preliminary plan.", "ready_for_brief":True,
              "updates":{"existing_use":{"value":"office","evidence":"office"}, "proposed_use":{"value":"restaurant","evidence":"restaurant"},
              "business_use":{"value":"yes","evidence":"convert office to restaurant"}}})}}]}).encode()
        with patch.object(app,"OPENAI_API_KEY","test"), patch("app.urllib.request.urlopen",return_value=Response()), patch.object(app,"research_context",return_value={"sources":[],"fees":[],"property":{"status":"needs_address"}}):
            result=app.openai_chat([{"role":"user","text":"convert office to restaurant"}],empty_project())
        self.assertEqual(result["facts"]["business_use"],"yes")
        self.assertEqual(result["facts"]["occupancy_change"],"unknown")
        self.assertEqual(result["stage"],"follow_up")
        self.assertEqual(result["question_field"],"location")

    def test_use_change_flags_mobility_even_without_site_work(self):
        brief=build_brief(project({"business_use":"yes","site_work":"no","proposed_use":"restaurant"}))
        mobility=next(step for step in brief["action_plan"] if step["id"]=='mobility')
        self.assertEqual(mobility["status"],"unresolved")
        self.assertIn('not the actual mobility fee',mobility["exceptions"])


class DeploymentConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.environment=patch.dict(os.environ,{"PROJECT_STORAGE_MODE":"persistent","VERCEL":"1","UPSTASH_REDIS_REST_URL":"","UPSTASH_REDIS_REST_TOKEN":"",
                                               "KV_REST_API_URL":"","KV_REST_API_TOKEN":""})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    def test_missing_storage_is_reported_before_model_calls(self):
        with patch.object(app,"openai_chat") as model:
            with self.assertRaises(project_store.StorageError) as raised:
                app.chat_request({"message":"A restaurant renovation"})
            self.assertEqual(raised.exception.code,"storage_not_configured")
            model.assert_not_called()

    def test_vercel_kv_aliases_are_supported(self):
        with patch.dict(os.environ,{"KV_REST_API_URL":"https://example.upstash.io","KV_REST_API_TOKEN":"test-token"}):
            project_store.validate_configuration()
            self.assertEqual(project_store.redis_credentials(),("https://example.upstash.io","test-token"))

    def test_partial_credentials_are_not_mixed_between_stores(self):
        with patch.dict(os.environ,{"UPSTASH_REDIS_REST_URL":"https://first.upstash.io","KV_REST_API_TOKEN":"other-store-token"}):
            with self.assertRaises(project_store.StorageError):project_store.validate_configuration()

    def test_readiness_exposes_no_credential_values(self):
        with patch.object(app,"OPENAI_API_KEY","private-model-key"),patch.dict(os.environ,{"UPSTASH_REDIS_REST_URL":"https://private.upstash.io","UPSTASH_REDIS_REST_TOKEN":"private-store-token"}):
            result=app.deployment_readiness()
            self.assertTrue(result["ready"])
            self.assertNotIn("private",json.dumps(result))

    def test_missing_model_configuration_has_distinct_error(self):
        with patch.object(app,"OPENAI_API_KEY",""),patch.dict(os.environ,{"VERCEL":""}):
            with self.assertRaises(app.ConfigurationError) as raised:
                app.chat_request({"message":"A restaurant renovation"})
            self.assertEqual(raised.exception.code,"model_not_configured")


if __name__ == "__main__":
    unittest.main()
