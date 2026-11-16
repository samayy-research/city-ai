"""Ash Site uses property-details attributes and stays separate from Historic."""
import unittest
from unittest.mock import patch

import historic_gis as gis
import app
from project_workflow import empty_project, update_project, build_brief


def feature(field, value, identity='071416 0005'):
    return {'attributes': {field: value, 'RE' if field == 'ASH_NAME' else 'ADDRESS': identity}}


class AshTests(unittest.TestCase):
    def test_blank_ash_fields_cannot_be_reported_as_no_contamination(self):
        ash = gis.screen_ash([feature('ASH_NAME', '')], [feature('ASHSITE', None)], True)
        context = {'historic': {'ash': ash}}
        issues = app.reply_issues({'message': 'No Ash Site contamination is shown.'}, empty_project(), 'check the site', context)
        self.assertTrue(any('contamination' in issue for issue in issues))
        self.assertFalse(app.reply_issues({'message': ash['reason']}, empty_project(), 'check the site', context))

    def test_designation_matches_map_address_and_parcel_fields(self):
        ash = gis.screen_ash([feature('ASH_NAME', 'Cleveland2')], [feature('ASHSITE', 'Cleveland2', '1632 MAIN ST N')], True)
        self.assertEqual(ash['status'], 'identified')
        self.assertEqual(ash['names'], ['Cleveland2'])
        self.assertEqual({record['field'] for record in ash['records']}, {'ASH_NAME', 'ASHSITE'})
        self.assertIn('Ash Program', ash['reason'])

    def test_three_blank_parcels_and_null_address_are_na_not_clearance(self):
        ash = gis.screen_ash([feature('ASH_NAME', ' ', str(i)) for i in range(3)], [feature('ASHSITE', None, '6100 PHILIPS HWY')], True)
        self.assertEqual(ash['status'], 'not_identified')
        self.assertEqual(len(ash['records']), 4)
        self.assertIn('not a soil-contamination clearance', ash['reason'])
        self.assertTrue(all(record['display_value'] == 'N/A' for record in ash['records']))

    def test_missing_field_or_failed_address_check_remains_unresolved(self):
        for parcels, addresses, complete in [([feature('ASH_NAME', ' ')], [], False),
                                               ([{'attributes': {'RE': '123456 0000'}}], [feature('ASHSITE', None)], True)]:
            with self.subTest(parcels=parcels):
                ash = gis.screen_ash(parcels, addresses, complete)
                self.assertEqual(ash['status'], 'requires_review')
                self.assertFalse(ash['complete'])

    def test_positive_on_one_of_multiple_parcels_survives_partial_failure(self):
        ash = gis.screen_ash([feature('ASH_NAME', 'Cleveland2'), {'attributes': {'RE': 'another'}}], [], False)
        self.assertEqual(ash['status'], 'identified')
        self.assertFalse(ash['complete'])

    def test_address_point_query_uses_exact_official_address_not_empty_re_field(self):
        parcels = [{'attributes': {'RE': '071416 0005', 'LONGNAME': '1632 MAIN ST N', 'ASH_NAME': 'Cleveland2'}}]
        with patch.object(gis, '_features', return_value={'features': [feature('ASHSITE', 'Cleveland2', '1632 MAIN ST N')]}) as query:
            ash = gis.lookup_ash(object(), parcels, {4: 'Address Point'})
        self.assertEqual(query.call_args.args[1], 4)
        self.assertEqual(query.call_args.args[2]['where'], "ADDRESS = '1632 MAIN ST N'")
        self.assertTrue(ash['complete'])

    def test_ash_action_is_separate_from_historic(self):
        p = update_project(empty_project(), {'location': {'value': '1632 MAIN ST N', 'evidence': '1632 MAIN ST N'}}, '1632 MAIN ST N')
        ash = gis.screen_ash([feature('ASH_NAME', 'Cleveland2')], [feature('ASHSITE', 'Cleveland2')], True)
        p['research'] = {'historic': {'query': '1632 MAIN ST N', 'ash': ash,
            'reason': 'No local designation identified.', 'local_status': 'not_identified',
            'checked_at': '2026-11-11T12:00:00+00:00', 'source_url': gis.MAP_URL}}
        self.assertFalse(gis.has_historic_flag(p['research']['historic']))
        plan = build_brief(p)
        step = next(step for step in plan['action_plan'] if step['id'] == 'ash')
        self.assertEqual(step['department'], 'Solid Waste Division / Ash Program')
        self.assertEqual(step['status'], 'unresolved')
        self.assertIn('Cleveland2', step['evidence'][0])
        p['research']['historic']['ash'] = gis.screen_ash([feature('ASH_NAME', '')], [feature('ASHSITE', None)], True)
        self.assertNotIn('ash', [step['id'] for step in build_brief(p)['action_plan']])


if __name__ == '__main__':
    unittest.main()
