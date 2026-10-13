"""Research provenance, fee qualification and informed-question regression cases."""
import unittest
from unittest.mock import patch

import city_research as research
from project_workflow import empty_project, update_project

FEES_HTML='''<h3>Zoning Counter</h3><table><tr><td>New Certification of Use or Relocation of Business</td><td>$112.00</td></tr></table>
<h3>Concurrency Management</h3><table><tr><td>Mobility Fee Calculation Certificate</td><td>$688.00</td></tr></table>'''
SEARCH_HTML='''<input type="hidden" name="__VIEWSTATE" value="test"><input type="hidden" name="__EVENTVALIDATION" value="test">'''
RESULTS_HTML='''<table><tr><td>080283-0005</td><td>701 SAN MARCO BLVD</td></tr><tr><td>080283-0010</td><td>701 SAN MARCO BLVD</td></tr></table>'''


class ResearchTests(unittest.TestCase):
    def setUp(self):
        with research._lock:research._cache.clear()

    def context(self):
        return {"sources":[{"id":"fees","title":"City fees","url":research.PAGES['fees'][1],"status":"available"}],
                "property":{"status":"needs_address"},"fees":research.fee_records(research.Page(FEES_HTML))}

    def test_fee_amount_is_fetched_and_keeps_the_category(self):
        fees=research.fee_records(research.Page(FEES_HTML))
        self.assertEqual(fees[0]['amount'],'$112.00')
        self.assertEqual(fees[1]['amount'],'$688.00')
        self.assertIn('not the project-specific mobility',fees[1]['qualification'])
        changed=research.fee_records(research.Page(FEES_HTML.replace('$112.00','$120.00')))
        self.assertEqual(changed[0]['amount'],'$120.00')

    def test_server_inserts_verified_fee_with_source_and_one_question(self):
        message,citations,disclosed=research.compose_response({"message":"A new restaurant may need a COU.","question":"Are you converting an office suite?",
                                                   "ready_for_brief":False,"fee_id":"cou-new","source_ids":[]},self.context())
        self.assertIn('$112.00',message)
        self.assertEqual(message.count('?'),1)
        self.assertEqual(citations[0]['id'],'fees')
        self.assertEqual(disclosed,'cou-new')

    def test_fee_is_not_repeated_without_a_direct_fee_question(self):
        result={"message":"The use still needs confirmation.","question":"What will change in the suite?", "ready_for_brief":False,"fee_id":"cou-new","source_ids":[]}
        message,_,disclosed=research.compose_response(result,self.context(),('cou-new',))
        self.assertNotIn('$112.00',message)
        self.assertEqual(disclosed,'')
        message,_,disclosed=research.compose_response(result,self.context(),('cou-new',),True)
        self.assertIn('$112.00',message)
        self.assertEqual(disclosed,'cou-new')

    def test_unverified_model_price_is_rejected(self):
        with self.assertRaises(ValueError):
            research.compose_response({"message":"Your total is $999.","ready_for_brief":True},self.context())

    def test_unavailable_or_unknown_fee_cannot_be_quoted(self):
        message,citations,_=research.compose_response({"message":"The review needs confirmation.","question":"What work is proposed?", "ready_for_brief":False,"fee_id":"invented","source_ids":["invented"]},
                                                   {"sources":[],"property":{},"fees":[]})
        self.assertNotIn('$',message);self.assertFalse(citations)

    def test_typo_lookup_keeps_office_and_parking_records_ambiguous(self):
        def fetched(url,opener=None,data=None):
            if 'Search.aspx' in url:return research.Page(SEARCH_HTML)
            if 'Results.aspx' in url:return research.Page(RESULTS_HTML)
            use='2892 Parking Garage' if '0005' in url else '1800 Office 3+ Sty'
            return research.Page('<h3>Primary Site Address</h3><p>701 SAN MARCO BLVD</p><p>Jacksonville FL 32207</p><table><tr><td>Property Use</td><td>'+use+'</td></tr></table>')
        with patch.object(research,'fetch',side_effect=fetched):result=research.lookup_property('701 San Marcio')
        self.assertEqual(result['status'],'ambiguous')
        self.assertEqual(len(result['records']),2)
        self.assertTrue(any('Office' in row['property_use'] for row in result['records']))
        self.assertGreater(result['match_score'],.9)

    def test_property_evidence_does_not_change_applicant_suite_use(self):
        record=update_project(empty_project(),{'location':{'value':'701 San Marco','evidence':'701 San Marco'}},'701 San Marco')
        property_result={'status':'matched','records':[{'property_use':'Office 3+ Sty'}]}
        with patch.object(research,'lookup_property',return_value=property_result),patch.object(research,'source_record',side_effect=lambda key:{'id':key,'status':'unavailable'}):
            context=research.research_context(record,'restaurant')
        self.assertEqual(context['property'],property_result)
        self.assertEqual(record['fields']['existing_use']['value'],'unknown')
        self.assertEqual(record['fields']['occupancy_change']['value'],'unknown')


if __name__=='__main__':unittest.main()
