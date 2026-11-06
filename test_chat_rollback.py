"""The original description updates and recovery of review-created sessions."""
import copy
import unittest
from unittest.mock import patch

import app
from project_workflow import empty_project, update_project, upgrade_project, build_brief


def initial_project():
    statement = 'building a restaurant at 701 san marco'
    return update_project(empty_project(), {key: {'value': value, 'evidence': statement}
        for key, value in {'project_description': 'building a restaurant', 'location': '701 san marco'}.items()}, statement)


def review_session():
    p = initial_project()
    quote = 'adding onto a building space on the rooftop totally new'
    p.update({'scope_schema_version': 2, 'derived_scope': {'building_work': {'value': 'yes'}},
              'scope_conflicts': {'project_description': {}}, 'pending_question': {'field': 'project_description'},
              'property_disclosed': 'old-key'})
    p['fields']['addition'] = {'value': 'yes', 'evidence': [quote]}
    p['fields']['project_type'] = {'value': 'addition', 'evidence': [quote]}
    p['conflicts']['project_description'] = {'previous': 'building a restaurant',
        'proposed': 'restaurant rooftop addition', 'evidence': quote}
    return p


class ChatRollbackTests(unittest.TestCase):
    def test_rooftop_detail_enriches_description_without_add_or_correction_flags(self):
        p = initial_project()
        quote = 'adding onto a building space on the rooftop totally new'
        updated = update_project(p, {'project_description': {'value': 'restaurant rooftop addition', 'evidence': quote},
                                    'project_type': {'value': 'addition', 'evidence': quote}}, quote)
        self.assertEqual(updated['fields']['project_description']['value'], 'restaurant rooftop addition')
        self.assertFalse(updated['conflicts'])

    def test_existing_review_sessions_drop_false_description_conflict_but_keep_real_fact_conflicts(self):
        p = review_session()
        p['conflicts']['plumbing'] = {'previous': 'yes', 'proposed': 'no', 'evidence': 'no plumbing'}
        snapshot = copy.deepcopy(p)
        upgraded = upgrade_project(p)
        self.assertNotIn('project_description', upgraded['conflicts'])
        self.assertIn('plumbing', upgraded['conflicts'])
        self.assertNotIn('addition', upgraded['fields'])
        self.assertNotIn('pending_question', upgraded)
        self.assertNotIn('scope_schema_version', upgraded)
        self.assertEqual(upgraded['fields']['project_type']['value'], 'addition')
        self.assertEqual(p, snapshot)
        self.assertEqual(upgrade_project(upgraded), upgraded)

    def test_followups_from_stuck_session_no_longer_repeat_description_conflict(self):
        p = review_session()
        for statement in ('i am adding the restaurant to the top floor', 'no', 'noooo'):
            updates = {'project_description': {'value': 'restaurant added to the top floor', 'evidence': statement}} if statement.startswith('i am') else {}
            with patch.object(app, 'OPENAI_API_KEY', 'test'), patch.object(app, 'research_context', return_value={
                    'property': {}, 'sources': [], 'fees': []}), patch.object(app, 'complete_json', side_effect=[
                    {'updates': updates}, {'ready_for_brief': False, 'message': 'I have the rooftop restaurant scope.',
                        'question': 'What is the planned floor area?', 'question_field': 'floor_area', 'source_ids': []}]):
                result = app.openai_chat([{'role': 'user', 'text': statement}], p)
            self.assertNotIn('replace the earlier description', result['message'])
            self.assertFalse(result['project']['conflicts'])
            p = result['project']

    def test_restored_final_plan_drops_reverted_scope_metadata(self):
        p = review_session()
        result = app.workspace_result({'project': p, 'stage': 'final', 'message': 'Saved plan',
                                       'brief': {'action_plan': [{'field': 'building_work'}]}})
        self.assertNotIn('scope_schema_version', result['project'])
        self.assertEqual(result['brief'], build_brief(result['project']))
        self.assertNotIn('building_work', [step['field'] for step in result['brief']['action_plan']])


if __name__ == '__main__':
    unittest.main()
