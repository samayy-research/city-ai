"""Interruptions must preserve project scope and the user's conversational focus."""
import unittest
from unittest.mock import patch

import app
from project_workflow import empty_project, update_project


def warehouse():
    values = {'location': '6100 PHILIPS HWY', 'project_description': 'reconstruction Philips Highway Warehouse'}
    return update_project(empty_project(), {key: {'value': value, 'evidence': value}
                          for key, value in values.items()}, ' | '.join(values.values()))


class ConversationTests(unittest.TestCase):
    def turn(self, project, statement, extraction, response, context=None, history=None):
        context = context or {'property': {}, 'sources': [], 'fees': []}
        with patch.object(app, 'OPENAI_API_KEY', 'test'), patch.object(app, 'research_context', return_value=context), patch.object(
                app, 'complete_json', side_effect=[extraction, response]) as model:
            result = app.openai_chat([*(history or []), {'role': 'user', 'text': statement}], project)
        self.assertIn('Eligible questions', model.call_args.args[0][1]['content'])
        return result

    def test_warehouse_address_research_and_resume_do_not_skip_building_type(self):
        p = warehouse()
        p['fields']['location'] = {'value': 'unknown', 'evidence': []}
        p['pending_intake_field'] = 'project_type'
        p['intake_asked_fields'] = ['project_type']
        first = self.turn(p, '6100 philips hwy', {'turn_intent': 'answer', 'updates': {
            'location': {'value': '6100 philips hwy', 'evidence': '6100 philips hwy'}}}, {
            'message': 'I found the address.', 'ready_for_brief': False,
            'question_field': 'interior_work', 'question': 'Will you change the layout?'})
        self.assertEqual(first['question_field'], 'project_type')
        historic = {'query': '6100 philips hwy', 'source_url': 'https://maps.coj.net/landdevelopmentreview/',
                    'checked_at': '2026-11-11T15:00:00+00:00',
                    'local_status': 'requires_review', 'reason': 'Three parcels checked; some layers unavailable.',
                    'parcels': [{'parcel': str(i), 'checks': []} for i in range(3)]}
        text = 'The check covered all three parcels, but some historic layers were unavailable. Their status remains unresolved.'
        second = self.turn(first['project'], 'check all 3 parcels', {'turn_intent': 'research', 'updates': {}}, {
            'message': text, 'ready_for_brief': False, 'conversation_action': 'respond', 'question': '',
            'source_ids': ['historic-gis']}, {'property': {}, 'sources': [], 'fees': [], 'historic': historic})
        self.assertEqual(second['message'], text)
        self.assertEqual(second['stage'], 'follow_up')
        self.assertIsNone(second['brief'])
        self.assertEqual(second['question_field'], '')
        self.assertEqual(second['project']['pending_intake_field'], 'project_type')
        self.assertFalse(second['project']['conversation']['awaiting_answer'])
        self.assertEqual(second['project']['fields']['interior_work']['value'], 'unknown')
        self.assertEqual(second['project']['fields']['plumbing']['value'], 'unknown')
        resumed = self.turn(second['project'], 'continue', {'turn_intent': 'question', 'updates': {}}, {
            'message': 'Let us return to the reconstruction scope.', 'ready_for_brief': False,
            'question_field': 'plumbing', 'question': 'Are any sinks planned?'})
        self.assertEqual(resumed['question_field'], 'project_type')
        self.assertTrue(resumed['project']['conversation']['awaiting_answer'])

    def test_direct_question_pauses_without_model_having_to_choose_respond(self):
        p = warehouse()
        p['pending_intake_field'] = 'project_type'
        result = self.turn(p, 'Why is Historic involved?', {'turn_intent': 'question', 'updates': {}}, {
            'message': 'Historic review can also concern archaeology; the actual comments are needed to explain this case.',
            'ready_for_brief': True, 'question_field': 'plumbing', 'question': 'Any sinks?'})
        self.assertEqual(result['question_field'], '')
        self.assertNotIn('Any sinks', result['message'])
        self.assertEqual(result['project']['pending_intake_field'], 'project_type')

    def test_explicit_map_request_overrides_a_misclassified_answer(self):
        p = warehouse()
        app.update_conversation(p, {'turn_intent': 'answer', 'updates': {}}, 'check all 3 parcels')
        self.assertEqual(p['conversation']['turn_intent'], 'research')

    def test_gis_response_checks_reject_broad_clearance_and_unrequested_low_detail(self):
        context = {'historic': {'local_status': 'not_identified'}, 'historic_needs_explanation': True}
        issues = app.reply_issues({'message': 'The site is not historic with low sensitivity and no historic review.',
                                  'source_ids': []}, warehouse(), '6100 philips hwy', context)
        self.assertEqual(len(issues), 3)
        self.assertFalse(app.reply_issues({'message': 'No mapped local landmark or district was identified; staff confirm review requirements.',
                                          'source_ids': ['historic-gis']}, warehouse(), '6100 philips hwy', context))

    def test_changed_failed_layer_check_requires_a_new_explanation(self):
        before = {'query': '6100 philips hwy', 'local_status': 'not_identified', 'reason': 'All layers checked.'}
        after = {**before, 'reason': 'Some layers unavailable.'}
        self.assertNotEqual(app.historic_fingerprint(before), app.historic_fingerprint(after))
        self.assertEqual(app.historic_fingerprint(before), app.historic_fingerprint({**before, 'checked_at': 'later'}))

    def test_address_correction_preserves_unanswered_scope_and_invalidates_old_research(self):
        p = warehouse()
        p['pending_intake_field'] = 'project_type'
        p['research'] = {'historic': {'query': '6100 PHILIPS HWY'}}
        p = update_project(p, {'location': {'value': '6200 PHILIPS HWY', 'evidence': '6200 PHILIPS HWY',
                                          'correction': True}}, '6200 PHILIPS HWY')
        app.update_conversation(p, {'turn_intent': 'correction', 'updates': {}}, '6200 PHILIPS HWY')
        self.assertNotIn('research', p)
        self.assertEqual(p['pending_intake_field'], 'project_type')
        self.assertEqual(app.apply_question_policy({'ready_for_brief': True}, p)['question_field'], 'project_type')

    def test_model_wording_and_relevant_topic_survive_validation(self):
        p = warehouse()
        response = {'message': 'Let us clarify the work around the warehouse.', 'ready_for_brief': False,
                    'question_field': 'site_work', 'question': 'Will you change the warehouse parking area?'}
        result = app.apply_question_policy(response, p)
        self.assertEqual(result['question'], response['question'])
        self.assertEqual(result['question_field'], 'site_work')

    def test_explicit_deferral_and_uncertainty_close_only_the_named_topic(self):
        p = warehouse()
        p['pending_intake_field'] = 'project_type'
        app.update_conversation(p, {'turn_intent': 'answer', 'deferred_fields': {'project_type': 'skip that for now'}},
                                'skip that for now')
        self.assertNotIn('pending_intake_field', p)
        self.assertNotIn('project_type', [item[0] for item in app.eligible_questions(p)])
        self.assertIn('plumbing', [item[0] for item in app.eligible_questions(p)])
        other = warehouse()
        app.update_conversation(other, {'deferred_fields': {'plumbing': 'skip plumbing'}}, 'check the map')
        self.assertIn('plumbing', [item[0] for item in app.eligible_questions(other)])

    def test_multi_answer_and_scope_change_invalidate_old_pending_branch(self):
        p = warehouse()
        p['pending_intake_field'] = 'interior_work'
        result = self.turn(p, 'new building with sinks', {'turn_intent': 'answer', 'updates': {
            'plumbing': {'value': 'yes', 'evidence': 'sinks'}}}, {
            'message': 'I have recorded a new building with sinks.', 'ready_for_brief': False,
            'question_field': 'specialized_review', 'question': 'What electrical systems are planned?'})
        self.assertEqual(result['question_field'], 'specialized_review')
        self.assertEqual(result['project']['fields']['plumbing']['value'], 'yes')

    def test_bare_yes_after_response_only_does_not_answer_suspended_question(self):
        p = warehouse()
        p['pending_intake_field'] = 'plumbing'
        p['conversation'] = {'awaiting_answer': False}
        result = self.turn(p, 'yes', {'turn_intent': 'answer', 'updates': {
            'plumbing': {'value': 'yes', 'evidence': 'yes'}}}, {
            'message': 'To clarify the plumbing scope.', 'ready_for_brief': False,
            'question_field': 'plumbing', 'question': 'Are any sinks planned?'})
        self.assertEqual(result['project']['fields']['plumbing']['value'], 'unknown')
        self.assertEqual(result['question_field'], 'plumbing')

    def test_explicit_finish_keeps_unknowns_for_city_and_does_not_invent_answers(self):
        p = warehouse()
        p['pending_intake_field'] = 'project_type'
        result = self.turn(p, 'give me the plan now', {'turn_intent': 'finish', 'updates': {}}, {
            'message': 'Your preliminary plan includes the unresolved scope for City confirmation.',
            'conversation_action': 'finish', 'ready_for_brief': True, 'question': ''})
        self.assertEqual(result['stage'], 'final')
        self.assertEqual(result['project']['fields']['project_type']['value'], 'unknown')
        self.assertTrue(result['brief']['action_plan'])

    def test_site_narrative_records_decision_and_recovers_from_repeated_model_question(self):
        p = warehouse()
        p = update_project(p, {key: {'value': value, 'evidence': value} for key, value in {
            'project_type': 'new_construction', 'plumbing': 'yes', 'specialized_review': 'no'}.items()},
            'new_construction yes no')
        p['pending_intake_field'] = 'site_work'
        p['conversation'] = {'awaiting_answer': True}
        wrong = {'message': 'Please clarify site work.', 'conversation_action': 'ask', 'ready_for_brief': False,
                 'question_field': 'site_work', 'question': 'Any landscaping or drainage?'}
        statement = 'gradining and parking\\'
        with patch.object(app, 'OPENAI_API_KEY', 'test'), patch.object(app, 'research_context', return_value={
                'property': {}, 'sources': [], 'fees': []}), patch.object(app, 'complete_json', side_effect=[
                {'updates': {'site_work': {'value': 'grading and parking', 'evidence': statement}}}, wrong, wrong]):
            result = app.openai_chat([{'role': 'user', 'text': statement}], p)
        self.assertEqual(result['project']['fields']['site_work']['value'], 'yes')
        self.assertIn('gradining and parking', result['project']['fields']['site_changes']['value'])
        self.assertEqual(result['question_field'], 'right_of_way')
        self.assertIn('recorded', result['message'])

    def test_old_stuck_session_recovers_grading_answer_without_negating_it_on_nbo(self):
        p = warehouse()
        p['pending_intake_field'] = 'site_work'
        question = next(question for field, question, _ in app.NEW_CONSTRUCTION_QUESTIONS if field == 'site_work')
        history = [{'role': 'model', 'text': question}, {'role': 'user', 'text': 'gradining and parking\\'},
                   {'role': 'model', 'text': 'Any landscaping or drainage?', 'question_field': 'site_work'}]
        result = self.turn(p, 'nbo', {'updates': {'site_work': {'value': 'no', 'evidence': 'nbo'}}}, {
            'message': 'The grading and parking scope remains recorded.', 'ready_for_brief': False}, history=history)
        self.assertEqual(result['project']['fields']['site_work']['value'], 'yes')
        self.assertNotIn('site_work', result['project']['conflicts'])
        self.assertNotEqual(result['question_field'], 'site_work')

    def test_none_and_yes_all_of_them_resolve_only_pending_decision(self):
        for statement, expected in [('none', 'no'), ('yes all of them', 'yes'), ('nbo', 'no')]:
            with self.subTest(statement=statement):
                p = warehouse()
                p['pending_intake_field'] = 'specialized_review'
                result = self.turn(p, statement, {'updates': {}}, {'message': 'The reported systems scope is recorded.',
                    'ready_for_brief': False})
                self.assertEqual(result['project']['fields']['specialized_review']['value'], expected)
                self.assertEqual(result['project']['fields']['site_work']['value'], 'unknown')

    def test_zoning_cou_intro_is_written_once_and_survives_prose_failure(self):
        p = warehouse()
        context = {'property': {}, 'sources': [{'id': 'cou', 'title': 'COU guidance', 'status': 'available',
                    'url': 'https://www.jacksonville.gov/'}], 'fees': []}
        wrong = {'message': 'Let us discuss plumbing.', 'conversation_action': 'ask', 'ready_for_brief': False,
                 'question_field': 'plumbing', 'question': 'Any sinks?'}
        with patch.object(app, 'OPENAI_API_KEY', 'test'), patch.object(app, 'research_context', return_value=context), patch.object(
                app, 'complete_json', side_effect=[{'updates': {}}, wrong, wrong]):
            result = app.openai_chat([{'role': 'user', 'text': 'continue'}], p)
        self.assertTrue(app.use_intro_present(result['message']))
        self.assertTrue(result['project']['use_intro_disclosed'])
        self.assertIn('cou', [source['id'] for source in result['citations']])
        followup = self.turn(result['project'], 'new building', {'updates': {}}, {
            'message': 'I have recorded the new building scope.', 'ready_for_brief': False}, context=context)
        self.assertFalse(context['needs_use_intro'])


if __name__ == '__main__':
    unittest.main()
