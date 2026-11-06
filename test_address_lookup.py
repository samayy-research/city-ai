"""Bounded official GIS address resolution and address confirmation."""
import unittest
from unittest.mock import MagicMock, patch

import app
import historic_gis as gis
from project_workflow import empty_project, update_project, routing_facts, build_brief


def feature(address, number='080283 0010', geometry=False):
    result = {'attributes': {'RE': number, 'LONGNAME': address}}
    if geometry:
        result['geometry'] = {'rings': [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    return result


def project(address):
    statement = address + ' restaurant'
    return update_project(empty_project(), {
        'location': {'value': address, 'evidence': address},
        'project_description': {'value': 'restaurant', 'evidence': 'restaurant'}}, statement)


class AddressResolutionTests(unittest.TestCase):
    def test_missing_suffix_uses_official_full_address(self):
        result = gis.select_address('701 San Marco', [feature('701 SAN MARCO BLVD')])
        self.assertEqual(result['resolved_address'], '701 SAN MARCO BLVD')
        self.assertEqual(result['address_match'], 'completed')
        self.assertFalse(result['address_confirmation_required'])

    def test_user_spelling_variants_are_suggestions_not_confirmed_locations(self):
        for query in ('701 San Maroi', '701 San Marcoi Blvd', '701 San Mro Blvd', '701 San Marcio'):
            with self.subTest(query=query):
                result = gis.select_address(query, [feature('701 SAN MARCO BLVD')])
                self.assertEqual(result['resolved_address'], '701 SAN MARCO BLVD')
                self.assertTrue(result['address_confirmation_required'])

    def test_duplicate_parcels_do_not_make_the_same_street_address_ambiguous(self):
        result = gis.select_address('701 San Marco', [feature('701 SAN MARCO BLVD'), feature('701 SAN MARCO BLVD', '080283 0005')])
        self.assertEqual(result['address_match'], 'completed')

    def test_different_street_types_remain_ambiguous_when_suffix_missing(self):
        result = gis.select_address('701 San Marco', [feature('701 SAN MARCO BLVD'), feature('701 SAN MARCO ST')])
        self.assertEqual(result['address_match'], 'ambiguous')
        self.assertNotIn('resolved_address', result)

    def test_explicit_number_suffix_and_direction_cannot_be_silently_changed(self):
        for query in ('702 San Marco Blvd', '701 San Marco St', '701 N San Marco Blvd'):
            with self.subTest(query=query):
                self.assertEqual(gis.select_address(query, [feature('701 SAN MARCO BLVD')])['address_match'], 'not_found')

    def test_close_spelling_candidates_require_confirmation_instead_of_guessing(self):
        result = gis.select_address('701 San Maro', [feature('701 SAN MARCO BLVD'), feature('701 SAN MARIO BLVD')])
        self.assertEqual(result['address_match'], 'ambiguous')

    def lookup(self, query, addresses=None, hit_layer=None, candidate_count=None, fail_candidates=False):
        addresses = addresses or ['701 SAN MARCO BLVD']
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.geturl.return_value = gis.MAP_URL
        metadata = {'layers': [{'id': 1, 'name': 'Parcels'}, *[{'id': key, 'name': title} for key, title in gis.LAYERS.items()]]}
        calls = []
        def features(client, layer, params):
            calls.append((layer, params.copy()))
            if layer == 1 and ' LIKE ' in params['where']:
                if fail_candidates:
                    raise ValueError('Incomplete GIS result')
                self.assertEqual(params['returnGeometry'], 'false')
                self.assertEqual(params['resultRecordCount'], 201)
                return {'features': [feature(address) for address in addresses] if candidate_count is None else [feature(addresses[0])] * candidate_count}
            if layer == 1:
                return {'features': [feature(address, geometry=True) for address in addresses
                                      if params['where'] == "LONGNAME = '" + address + "'"]}
            return {'features': [{'attributes': {'NAME': 'Mapped resource'}}] if layer == hit_layer else []}
        with patch.object(gis.urllib.request, 'build_opener', return_value=opener), patch.object(gis, '_json', return_value=metadata), patch.object(gis, '_features', side_effect=features):
            return gis.lookup_historic(query), calls

    def test_missing_suffix_runs_all_screens_on_resolved_geometry_and_preserves_query(self):
        result, calls = self.lookup('701 San Marco')
        self.assertEqual(result['query'], '701 San Marco')
        self.assertEqual(result['resolved_address'], '701 SAN MARCO BLVD')
        self.assertEqual(result['local_status'], 'not_identified')
        self.assertEqual(len(result['screening_checks']), 6)
        self.assertEqual({layer for layer, _ in calls if layer != 1}, set(gis.LAYERS))
        self.assertIn('official GIS address', result['reason'])

    def test_exact_address_does_not_use_candidate_search(self):
        result, calls = self.lookup('701 San Marco Blvd')
        self.assertEqual(result['address_match'], 'exact')
        self.assertEqual(sum(layer == 1 for layer, _ in calls), 1)

    def test_nearby_number_runs_screening_and_requires_address_confirmation(self):
        result, calls = self.lookup('703 San Marco')
        self.assertEqual(result['resolved_address'], '701 SAN MARCO BLVD')
        self.assertEqual(result['address_match'], 'nearest')
        self.assertTrue(result['address_confirmation_required'])
        self.assertEqual(result['historic_status'], 'requires_review')
        self.assertEqual({layer for layer, _ in calls if layer != 1}, set(gis.LAYERS))
        self.assertIn('nearby address', result['reason'])
        p = project('703 San Marco')
        p['research'] = {'historic': result}
        self.assertEqual(app.apply_question_policy({'ready_for_brief': True}, p)['question'],
                         'Did you mean 701 SAN MARCO BLVD?')
        self.assertEqual(routing_facts(p)['historic_review'], 'not_sure')

    def test_nearest_number_preserves_positive_flags(self):
        result, _ = self.lookup('703 San Marco', hit_layer=43)
        self.assertTrue(result['historic_flag'])
        self.assertTrue(result['address_confirmation_required'])

    def test_nearest_number_rejects_ties_different_streets_and_distant_numbers(self):
        for query, addresses, expected in (
            ('703 San Marco', ['701 SAN MARCO BLVD', '705 SAN MARCO BLVD'], 'ambiguous'),
            ('703 San Marco', ['701 SAN MARCO BLVD', '709 SAN MARCO ST'], 'ambiguous'),
            ('703 San Marco St', ['701 SAN MARCO BLVD'], 'not_found'),
            ('703 N San Marco Blvd', ['701 SAN MARCO BLVD'], 'not_found'),
            ('999 San Marco', ['701 SAN MARCO BLVD'], 'not_found'),
        ):
            with self.subTest(query=query, addresses=addresses):
                result = gis.nearest_address(query, [feature(address) for address in addresses])
                self.assertEqual(result['address_match'], expected)
                self.assertNotIn('resolved_address', result)

    def test_unconfirmed_typo_negative_does_not_exclude_historic_route(self):
        result, _ = self.lookup('701 San Maroi')
        self.assertEqual(result['local_status'], 'not_identified')
        self.assertEqual(result['historic_status'], 'requires_review')
        p = project('701 San Maroi')
        p['fields']['exterior_work'] = {'value': 'no', 'evidence': ['no outside work']}
        p['research'] = {'historic': result}
        self.assertEqual(routing_facts(p)['historic_review'], 'not_sure')
        self.assertEqual(next(step for step in build_brief(p)['action_plan'] if step['id'] == 'historic')['status'], 'unresolved')

    def test_typo_positive_preserves_screening_flags_and_qualification(self):
        result, _ = self.lookup('701 San Maroi', hit_layer=43)
        self.assertTrue(result['historic_flag'])
        self.assertEqual(result['status'], 'requires_review')
        self.assertIn('suggested address', result['reason'])

    def test_archaeology_plan_keeps_suggested_address_qualification(self):
        finding, _ = self.lookup('701 San Maroi')
        finding['archaeology'] = {'levels': ['High'], 'source_url': gis.MAP_URL}
        p = project('701 San Maroi')
        p['research'] = {'historic': finding}
        step = next(step for step in build_brief(p)['action_plan'] if step['id'] == 'archaeology')
        self.assertEqual(step['status'], 'unresolved')
        self.assertIn('701 SAN MARCO BLVD (address not confirmed)', step['evidence'][0])

    def test_ambiguous_incomplete_and_overlarge_candidate_searches_do_not_screen_a_guess(self):
        for kwargs in ({'addresses': ['701 SAN MARCO ST', '701 SAN MARCO BLVD']}, {'candidate_count': 201}, {'fail_candidates': True}):
            with self.subTest(kwargs=kwargs):
                result, calls = self.lookup('701 San Marco', **kwargs)
                self.assertEqual(result['local_status'], 'requires_review')
                self.assertFalse(result['parcels'])
                self.assertTrue(all(layer == 1 for layer, _ in calls))

    def test_confirmation_question_is_asked_once_per_suggestion(self):
        finding, _ = self.lookup('701 San Maroi')
        p = project('701 San Maroi')
        p['research'] = {'historic': finding}
        first = app.apply_question_policy({'ready_for_brief': True}, p)
        self.assertEqual(first['question'], 'Did you mean 701 SAN MARCO BLVD?')
        p['address_confirmation_asked_for'] = first['address_confirmation_key']
        second = app.apply_question_policy({'ready_for_brief': True}, p)
        self.assertNotEqual(second.get('question_kind'), 'address_confirmation')
        self.assertEqual(p['fields']['location']['value'], '701 San Maroi')

    def test_chat_preserves_suggestion_confirmation_and_does_not_repeat_scope(self):
        finding, _ = self.lookup('701 San Maroi')
        statement = 'I am building a new restaurant at 701 San Maroi'
        with patch.object(app, 'OPENAI_API_KEY', 'test'), patch.object(app, 'complete_json', side_effect=[
                {'updates': {'location': {'value': '701 San Maroi', 'evidence': '701 San Maroi'}, 'project_description': {'value': 'building a new restaurant', 'evidence': 'building a new restaurant'}}},
                {'ready_for_brief': True, 'message': 'Project recorded.', 'question': ''}]), patch.object(app, 'research_context', return_value={
                    'sources': [], 'fees': [], 'property': {}, 'historic': finding}):
            result = app.openai_chat([{'role': 'user', 'text': statement}], empty_project())
        self.assertEqual(result['stage'], 'follow_up')
        self.assertEqual(result['question_field'], 'location')
        self.assertIn('Did you mean 701 SAN MARCO BLVD?', result['message'])
        self.assertIn('provisional', result['message'])
        self.assertNotIn('What would you like to build', result['message'])
        self.assertIn('address_confirmation_asked_for', result['project'])
        self.assertEqual(result['project']['fields']['location']['value'], '701 San Maroi')

    def test_suggestion_confirmation_updates_location_and_invalidates_old_result(self):
        finding, _ = self.lookup('701 San Maroi')
        p = project('701 San Maroi')
        p['research'] = {'historic': finding}
        p['address_confirmation_asked_for'] = 'old-key'
        p = update_project(p, {'location': {'value': '701 SAN MARCO BLVD', 'evidence': 'Yes, that address', 'correction': True}}, 'Yes, that address')
        self.assertEqual(p['fields']['location']['value'], '701 SAN MARCO BLVD')
        self.assertNotIn('research', p)
        self.assertNotIn('address_confirmation_asked_for', p)


if __name__ == '__main__':
    unittest.main()
