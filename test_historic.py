"""Historic GIS screening, provenance, address changes and conversation behavior."""
import copy
from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock, patch

import app
import city_research
import historic_gis as gis
from project_workflow import empty_project, update_project, routing_facts, intake_progress, build_brief


def project(address='117 W Duval St'):
    return update_project(empty_project(), {'location':{'value':address,'evidence':address},
        'project_description':{'value':'restaurant project','evidence':'restaurant project'}}, address+' restaurant project')


def finding(record, status='designated'):
    return {'query':record['fields']['location']['value'],'source_url':gis.MAP_URL,
            'schema_version':gis.SCHEMA_VERSION,
            'checked_at':datetime.now(timezone.utc).isoformat(), 'local_status':status,
            'status':status,'reason':'Mapped local historic finding.','parcels':[]}


class HistoricTests(unittest.TestCase):
    def lookup(self, hits=None, fail_layer=None, parcel_count=1, archaeology_levels=None):
        metadata={'layers':[{'id':1,'name':'Parcels'}, *[{'id':key,'name':value} for key,value in gis.LAYERS.items()]]}
        opener=MagicMock()
        opener.open.return_value.__enter__.return_value.geturl.return_value=gis.MAP_URL
        def features(client, layer, params):
            if layer==1:
                self.assertEqual(params['outSR'],'3857')
                return {'features':[{'attributes':{'RE':f'123456 {index:04}', 'LONGNAME':'117 DUVAL ST W'},
                    'geometry':{'rings':[[[0,0],[1,0],[1,1],[0,0]]]}} for index in range(parcel_count)]}
            self.assertEqual(params['geometryType'],'esriGeometryPolygon')
            self.assertEqual(params['spatialRel'],'esriSpatialRelIntersects')
            self.assertIn('rings',params['geometry'])
            if layer==fail_layer:
                raise OSError('service unavailable')
            if layer==40 and archaeology_levels:
                return {'features':[{'attributes':{'MODEL':level}} for level in archaeology_levels]}
            return {'features':[{'attributes':{'LANDMARK_N':'St James','ORDINANCE':'2001-0338'}}] if layer in (hits or []) else []}
        with patch.object(gis.urllib.request,'build_opener',return_value=opener),patch.object(gis,'_json',return_value=metadata),patch.object(gis,'_features',side_effect=features):
            return gis.lookup_historic('117 W. Duval Street, Jacksonville, FL')

    def test_local_positive_and_no_hits(self):
        self.assertEqual(self.lookup([43])['local_status'],'designated')
        negative=self.lookup()
        self.assertEqual(negative['local_status'],'not_identified')
        self.assertEqual(len(negative['parcels'][0]['checks']),6)

    def test_national_inventory_does_not_establish_local_designation(self):
        result=self.lookup([42,45])
        self.assertEqual(result['local_status'],'not_identified')
        self.assertEqual(result['status'],'requires_review')
        self.assertIn('alone do not establish',result['reason'])

    def test_111_riverside_archaeology_is_separate_from_local_designation(self):
        result=self.lookup(archaeology_levels=['High','Low','Water'])
        self.assertEqual(result['local_status'],'not_identified')
        self.assertEqual(result['status'],'requires_review')
        self.assertEqual(result['archaeology']['levels'],['High','Low','Water'])
        self.assertIn('predictive model',result['reason'])
        record=project()
        result['query']=record['fields']['location']['value']
        record['research']={'historic':result}
        self.assertEqual(routing_facts(record)['historic_review'],'not_sure')
        step=next(step for step in build_brief(record)['action_plan'] if step['id']=='archaeology')
        self.assertEqual(step['status'],'unresolved')
        self.assertIn('ground disturbance',step['title'])

    def test_failed_archaeology_query_does_not_become_a_negative(self):
        result=self.lookup(fail_layer=40)
        self.assertEqual(result['archaeology']['status'],'requires_review')
        self.assertEqual(result['local_status'],'not_identified')
        self.assertEqual(result['status'],'requires_review')

    def test_medium_and_low_sensitivity_do_not_add_flags_or_review_actions(self):
        for levels in (['Low'], ['Medium'], ['Medium','Low','Water']):
            with self.subTest(levels=levels):
                result=self.lookup(archaeology_levels=levels)
                self.assertFalse(result['archaeology']['flagged'])
                self.assertEqual(result['status'],'not_identified')
                self.assertNotIn('archaeological sensitivity',result['reason'])
                record=project()
                result['query']=record['fields']['location']['value']
                record['research']={'historic':result}
                self.assertNotIn('archaeology',[step['id'] for step in build_brief(record)['action_plan']])

    def test_failures_and_multiple_parcels_are_not_silent_negatives(self):
        self.assertEqual(self.lookup(fail_layer=43)['local_status'],'requires_review')
        multi=self.lookup(parcel_count=2)
        self.assertEqual(multi['status'],'requires_review')
        self.assertEqual(len(multi['parcels']),2)

    def test_invalid_address_does_not_request_gis(self):
        with patch.object(gis.urllib.request,'build_opener') as opener:
            self.assertEqual(gis.lookup_historic('not_sure')['local_status'],'requires_review')
            opener.assert_not_called()

    def test_gis_drives_route_without_overwriting_applicant_fields(self):
        record=project()
        record['research']={'historic':finding(record)}
        self.assertEqual(routing_facts(record)['historic_review'],'yes')
        self.assertEqual(record['fields']['historic_review']['value'],'unknown')
        result=app.apply_question_policy({'ready_for_brief':True},record)
        self.assertEqual(result['question_field'],'exterior_work')
        step=next(step for step in build_brief(record)['action_plan'] if step['id']=='historic')
        self.assertEqual(step['status'],'unresolved')
        self.assertEqual(step['evidence_label'],'City GIS')

    def test_negative_is_visible_and_no_designation_question_is_asked(self):
        record=project()
        record['research']={'historic':finding(record,'not_identified')}
        topic=next(topic for topic in intake_progress(record)['topics'] if topic['id']=='historic')
        self.assertEqual(topic['status'],'excluded')
        self.assertEqual(topic['status_label'],'No historic screening flags identified')
        response=app.apply_question_policy({'ready_for_brief':False,'question_field':'historic_review','question':'Is it historic?'},record)
        self.assertNotEqual(response['question_field'],'historic_review')

    def test_changed_address_ignores_old_historic_result(self):
        record=project()
        record['research']={'historic':finding(record)}
        record=update_project(record,{'location':{'value':'701 San Marco Blvd','evidence':'701 San Marco Blvd','correction':True}},'701 San Marco Blvd')
        self.assertEqual(gis.current_historic(record),{})
        self.assertEqual(routing_facts(record)['historic_review'],'unknown')

    def test_reuse_and_address_change_invalidate_lookup(self):
        record=project()
        record['research']={'historic':finding(record)}
        with patch.object(city_research,'source_record',side_effect=lambda key:{'id':key,'status':'unavailable'}),patch.object(city_research,'lookup_property',return_value={}),patch.object(city_research,'lookup_historic',return_value={}) as lookup:
            self.assertEqual(city_research.research_context(record,'hello')['historic']['local_status'],'designated')
            lookup.assert_not_called()
            record['fields']['location']['value']='701 San Marco Blvd'
            city_research.research_context(record,'hello')
            lookup.assert_called_once_with('701 San Marco Blvd')

    def test_json_transport_has_map_reference_and_rejects_arcgis_errors(self):
        opener=MagicMock()
        response=opener.open.return_value.__enter__.return_value
        response.geturl.return_value=gis.PROXY
        response.read.return_value=b'{"error":{"code":499}}'
        with self.assertRaises(ValueError):
            gis._json(opener,'/43/query',{'where':'1=1'})
        request=opener.open.call_args.args[0]
        self.assertEqual(request.get_header('Referer'),gis.MAP_URL)
        self.assertEqual(request.get_method(),'POST')

    def test_model_reply_gets_gis_facts_and_exterior_followup(self):
        record=project()
        historic=finding(record)
        context={'historic':historic, 'property':{}, 'sources':[], 'fees':[]}
        with patch.object(app,'OPENAI_API_KEY','test'),patch.object(app,'research_context',return_value=context),patch.object(app,'complete_json',side_effect=[{'updates':{}},{'message':'Your preliminary plan is ready.','ready_for_brief':True,'question':''}]) as model:
            result=app.openai_chat([{'role':'user','text':'Please continue.'}],record)
        self.assertEqual(result['question_field'],'exterior_work')
        self.assertEqual(result['stage'],'follow_up')
        self.assertIn('City map identifies a local historic designation',result['message'])
        self.assertIn('historic-gis',[source['id'] for source in result['citations']])
        self.assertEqual(result['project']['fields']['historic_review']['value'],'unknown')
        self.assertIn('research',model.call_args.args[0][1]['content'])

    def test_gis_and_applicant_disagreement_stays_unresolved(self):
        record=project()
        record['fields']['historic_review']={'value':'yes','evidence':['historic district']}
        record['research']={'historic':finding(record,'not_identified')}
        self.assertEqual(routing_facts(record)['historic_review'],'not_sure')

    def test_any_listed_category_triggers_the_overall_historic_flag(self):
        for layer in gis.LAYERS:
            with self.subTest(layer=layer):
                result=self.lookup([layer], archaeology_levels=['High'] if layer==40 else None)
                self.assertTrue(result['historic_flag'])
                self.assertEqual(result['historic_status'],'flagged')
                self.assertEqual(len(result['screening_checks']),6)
                record=project()
                result['query']=record['fields']['location']['value']
                record['research']={'historic':result}
                self.assertNotEqual(routing_facts(record)['historic_review'],'no')
                historic=next(step for step in build_brief(record)['action_plan'] if step['id']=='historic')
                self.assertNotEqual(historic['status'],'not_applicable')
                topic=next(topic for topic in intake_progress(record)['topics'] if topic['id']=='historic')
                self.assertEqual(topic['status_label'],'Historic review flagged')


if __name__=='__main__':
    unittest.main()
