"""Server-owned completion of intake before an applicant's first City meeting."""
import copy
import os
import unittest
from unittest.mock import patch

import app
import demo_session
from project_workflow import empty_project, update_project, construction_type, build_brief


def project(**overrides):
    values = {'location': '701 San Marco', 'project_description': 'restaurant with grill and sushi preparation',
              'project_type': 'existing_building',
              'interior_work': 'yes', 'plumbing': 'yes', 'specialized_review': 'yes',
              'site_work': 'no', 'right_of_way': 'no'}
    values.update(overrides)
    return update_project(empty_project(), {key: {'value': value, 'evidence': value}
        for key, value in values.items()}, ' | '.join(values.values()))


def cou_offer(field='existing_approvals'):
    return {'ready_for_brief': False, 'message': 'Your project scope can be discussed with the City.',
            'question': 'Have you confirmed the Certificate of Use steps with City staff?',
            'question_field': field, 'question_reason': 'Discuss COU steps.'}


class IntakeCompletionTests(unittest.TestCase):
    def test_new_business_alone_asks_building_type_before_prior_tenant_use(self):
        p = project(project_description='opening a brand new dispensary', project_type='unknown',
                    business_use='yes', existing_use='unknown', interior_work='unknown')
        result = app.apply_question_policy({'ready_for_brief': False, 'question_field': 'existing_use'}, p)
        self.assertEqual(result['question_field'], 'project_type')
        self.assertIn('ground up', result['question'])

    def test_construction_type_requires_building_scope_and_respects_negation(self):
        for text, expected in (
            ('opening a brand new dispensary', 'unknown'),
            ('its a whole new space from ground up', 'new_construction'),
            ('its a newwwwwwww building', 'new_construction'),
            ('not a new building, renovating an existing space', 'existing_building'),
            ('not from the ground up', 'unknown'),
            ('maybe a new building', 'unknown'),
            ('new sinks in an existing building', 'existing_building'),
        ):
            with self.subTest(text=text):
                self.assertEqual(construction_type(text), expected)

    def test_ground_up_reply_saves_branch_without_model_and_skips_renovation_questions(self):
        p = project(project_description='opening a brand new dispensary', project_type='unknown',
                    business_use='yes', existing_use='unknown', interior_work='unknown', plumbing='unknown')
        p['pending_intake_field'] = 'existing_use'  # Also repairs the older, incorrect path.
        statement = 'its a whole new space from ground up'
        with patch.object(app, 'OPENAI_API_KEY', 'test'), patch.object(app, 'research_context', return_value={
                'property': {}, 'sources': [], 'fees': []}), patch.object(app, 'complete_json', side_effect=[
                {'updates': {}}, {'ready_for_brief': False, 'message': 'The new building scope is recorded.',
                'question_field': 'interior_work', 'question': 'Will you change anything inside?'}]):
            result = app.openai_chat([{'role': 'user', 'text': statement}], p)
        self.assertEqual(result['project']['fields']['project_type']['value'], 'new_construction')
        self.assertEqual(result['question_field'], 'plumbing')
        self.assertIn('fixtures are planned', result['message'])
        self.assertNotIn('replace', result['message'])
        self.assertNotIn('interior_work', result['project']['intake_asked_fields'])
        self.assertEqual(result['project']['fields']['plumbing']['value'], 'unknown')
        self.assertEqual(result['project']['fields']['interior_work']['value'], 'unknown')
        plan = build_brief(result['project'])
        step = next(step for step in plan['action_plan'] if step['id'] == 'building')
        self.assertEqual(step['status'], 'applicable')
        self.assertIn(statement, step['evidence'])
        self.assertIn('new building', step['request'])

    def test_new_building_system_question_avoids_restaurant_and_replacement_language(self):
        p = project(project_type='new_construction', project_description='new dispensary building',
                    existing_use='unknown', interior_work='unknown', specialized_review='unknown')
        result = app.apply_question_policy({'ready_for_brief': True}, p)
        self.assertEqual(result['question_field'], 'specialized_review')
        self.assertNotIn('cooking', result['question'])
        self.assertNotIn('replace', result['question'])
        self.assertIn('new building', result['question'])

    def test_new_construction_finishes_without_old_use_or_interior_alteration_answers(self):
        p = project(project_type='new_construction', business_use='yes',
                    existing_use='unknown', interior_work='unknown', plumbing='no')
        result = app.apply_question_policy({'ready_for_brief': False}, p)
        self.assertTrue(result['ready_for_brief'])
        steps = {step['id']: step for step in build_brief(p)['action_plan']}
        self.assertEqual(steps['building']['status'], 'applicable')
        self.assertEqual(steps['plumbing']['status'], 'not_applicable')
        self.assertIn('new building', steps['occupancy']['request'])

    def test_existing_building_retains_previous_use_then_interior_questions(self):
        p = project(project_type='existing_building', business_use='yes', existing_use='unknown', interior_work='unknown')
        self.assertEqual(app.apply_question_policy({}, p)['question_field'], 'existing_use')
        p = update_project(p, {'existing_use': {'value': 'office', 'evidence': 'office'}}, 'office')
        self.assertEqual(app.apply_question_policy({}, p)['question_field'], 'interior_work')

    def test_exact_typo_message_keeps_scope_when_extractor_saves_only_location(self):
        statement = 'hi im renovatig a retsyuarnt at 701 san marco blv'
        for scope in (None, {'value': 'renovating a restaurant', 'evidence': 'renovating a restaurant'}):
            updates = {'location': {'value': '701 san marco blv', 'evidence': '701 san marco blv'}}
            if scope:
                updates['project_description'] = scope
            with self.subTest(scope=scope), patch.object(app, 'OPENAI_API_KEY', 'test'), patch.object(
                    app, 'research_context', return_value={'property': {}, 'sources': [], 'fees': []}) as research, patch.object(
                    app, 'complete_json', side_effect=[{'updates': updates}, {'ready_for_brief': True,
                        'message': 'Zoning and Certificate of Use coordination are early topics to confirm for the business.'}]):
                result = app.openai_chat([{'role': 'user', 'text': statement}], empty_project())
            self.assertEqual(result['project']['fields']['project_description']['value'], 'renovatig a retsyuarnt')
            self.assertIn(result['project']['fields']['project_description']['evidence'][0], statement)
            self.assertNotEqual(result['question_field'], 'project_description')
            self.assertNotIn('What would you like to build', result['message'])
            self.assertIn('Certificate of Use', result['message'])
            self.assertEqual(result['project']['revision'], 1)
            self.assertFalse(result['project']['conflicts'])
            self.assertEqual(result['project']['fields']['plumbing']['value'], 'unknown')
            research.assert_called_once()

    def test_raw_intent_fallback_does_not_guess_from_address_existing_use_or_hypothetical(self):
        for statement in ('701 San Marco', 'Hi im at 701 San Marco', 'This is an existing restaurant',
                          'Could I renovate a restaurant?', 'If we renovate a restaurant', 'I am not renovating a restaurant',
                          'I am building at 701 San Marco'):
            with self.subTest(statement=statement):
                self.assertFalse(app.first_stated_intent(statement))

    def test_scope_before_address_is_preserved_for_the_next_turn(self):
        with patch.object(app, 'OPENAI_API_KEY', 'test'), patch.object(app, 'research_context') as research, patch.object(
                app, 'complete_json', return_value={'updates': {}}):
            result = app.openai_chat([{'role': 'user', 'text': 'hi im renovatig a retsyuarnt'}], empty_project())
        self.assertEqual(result['question_field'], 'location')
        self.assertEqual(result['project']['fields']['project_description']['value'], 'renovatig a retsyuarnt')
        research.assert_not_called()

    def test_existing_description_is_not_replaced_by_first_message_recovery(self):
        p = project()
        with patch.object(app, 'OPENAI_API_KEY', 'test'), patch.object(app, 'research_context', return_value={
                'property': {}, 'sources': [], 'fees': []}), patch.object(app, 'complete_json', side_effect=[
                {'updates': {}}, {'ready_for_brief': True, 'message': 'The existing scope remains recorded.'}]):
            result = app.openai_chat([{'role': 'user', 'text': 'I am adding a rooftop space'}], p)
        self.assertEqual(result['project']['fields']['project_description'], p['fields']['project_description'])

    def test_complete_scope_finishes_even_when_model_keeps_asking_about_cou(self):
        p = project()
        before = copy.deepcopy(p)
        for field in ('existing_approvals', 'business_use', '', 'existing_use'):
            with self.subTest(field=field):
                result = app.apply_question_policy(cou_offer(field), p)
                self.assertIs(result['ready_for_brief'], True)
                self.assertEqual(result['question'], '')
                self.assertEqual(result['question_field'], '')
                self.assertEqual(result['question_reason'], '')
        self.assertEqual(p, before)
        self.assertEqual(p['fields']['existing_approvals']['value'], 'unknown')

    def test_explicit_uncertainty_allows_preliminary_checklist(self):
        result = app.apply_question_policy(cou_offer(), project(site_work='not_sure', right_of_way='not_sure'))
        self.assertTrue(result['ready_for_brief'])

    def test_already_asked_unknown_scope_remains_a_city_confirmation(self):
        p = project(plumbing='unknown')
        p['intake_asked_fields'] = ['plumbing']
        self.assertTrue(app.apply_question_policy(cou_offer(), p)['ready_for_brief'])

    def test_missing_scope_is_asked_instead_of_cou_procedures(self):
        p = project(plumbing='unknown')
        result = app.apply_question_policy(cou_offer(), p)
        self.assertFalse(result['ready_for_brief'])
        self.assertEqual(result['question_field'], 'plumbing')
        self.assertNotIn('Certificate of Use', result['question'])

    def test_procedural_question_cannot_hide_behind_a_scope_field(self):
        p = project(plumbing='unknown')
        result = app.apply_question_policy(cou_offer('plumbing'), p)
        self.assertIn('sinks', result['question'])

    def test_offer_of_more_help_cannot_extend_intake(self):
        result = cou_offer('floor_area')
        result['question'] = 'Would you like guidance on starting the process?'
        result = app.apply_question_policy(result, project(plumbing='unknown'))
        self.assertEqual(result['question_field'], 'plumbing')

    def test_model_factual_question_cannot_jump_ahead_of_checklist(self):
        question = {'ready_for_brief': False, 'message': 'The previous activity helps identify use review.',
                    'question': 'Was the space last used as an office or a restaurant?', 'question_field': 'existing_use'}
        result = app.apply_question_policy(question, project(plumbing='unknown'))
        self.assertEqual(result['question_field'], 'plumbing')
        self.assertEqual(result['message'], question['message'])
        self.assertIn('plumbing', result['question'])

    def test_recorded_restaurant_scope_prevents_generic_use_question(self):
        for description in ('building a restaurant', 'im building restaurantat',
                            'restaurant renovation', 'resdtauarant'):
            with self.subTest(description=description):
                p = project(project_description=description, site_work='unknown')
                self.assertEqual(p['fields']['proposed_use']['value'], 'unknown')
                result = app.apply_question_policy({'ready_for_brief': False,
                    'question_field': 'proposed_use', 'question': 'What will the space be used for?'}, p)
                self.assertEqual(result['question_field'], 'site_work')
                self.assertIn('parking', result['question'])
                self.assertEqual(p['fields']['proposed_use']['value'], 'unknown')

    def test_stated_renovation_prevents_generic_project_type_question(self):
        result = app.apply_question_policy({'ready_for_brief': False,
            'question_field': 'project_type', 'question': 'Is this new construction or a renovation?'},
            project(project_description='restaurant renovation inside the space', site_work='unknown'))
        self.assertEqual(result['question_field'], 'site_work')

    def test_transcript_systems_reply_advances_to_site_without_reasking_use_or_quoting_hood(self):
        p = project(project_description='building a restaurant', specialized_review='unknown',
                    site_work='unknown', right_of_way='unknown')
        statement = 'yes i will be doing all that'
        context = {'property': {}, 'sources': [], 'fees': [{'id': 'hood-review',
            'amount': '$125.00', 'label': 'Fire Plans Review - New Hood Systems',
            'qualification': 'Not a total project cost.'}]}
        with patch.object(app, 'OPENAI_API_KEY', 'test'), patch.object(app, 'research_context', return_value=context), patch.object(
                app, 'complete_json', side_effect=[{'updates': {'specialized_review': {'value': 'yes', 'evidence': statement}}},
                {'ready_for_brief': False, 'message': 'Equipment and system changes may affect trade reviews.',
                 'question_field': 'proposed_use', 'question': 'What will the space be used for?', 'fee_id': 'hood-review'}]):
            result = app.openai_chat([{'role': 'user', 'text': statement}], p)
        self.assertEqual(result['question_field'], 'site_work')
        self.assertNotIn('What will the space be used for', result['message'])
        self.assertNotIn('$125', result['message'])
        self.assertEqual(result['project']['fields']['specialized_review']['value'], 'yes')

    def test_direct_fee_question_still_gets_sourced_fee_during_intake(self):
        p = project(site_work='unknown')
        context = {'property': {}, 'sources': [], 'fees': [{'id': 'hood-review',
            'amount': '$125.00', 'label': 'Fire Plans Review - New Hood Systems',
            'qualification': 'This category does not establish whether a hood is required.'}]}
        with patch.object(app, 'OPENAI_API_KEY', 'test'), patch.object(app, 'research_context', return_value=context), patch.object(
                app, 'complete_json', side_effect=[{'updates': {}}, {'ready_for_brief': False,
                'message': 'The schedule includes a hood-review category if applicable.', 'fee_id': 'hood-review',
                'question_field': 'site_work', 'question': 'Any parking changes?'}]):
            result = app.openai_chat([{'role': 'user', 'text': 'What is the hood review fee?'}], p)
        self.assertIn('$125.00', result['message'])

    def test_bundled_interior_plumbing_and_parking_question_is_split(self):
        bundled = 'Do you plan any interior renovations, plumbing changes, or site/parking alterations with the restaurant build-out?'
        for field in ('interior_work', 'alterations', 'plumbing', 'existing_use', 'site_work'):
            with self.subTest(field=field):
                p = project(interior_work='unknown', plumbing='unknown')
                result = app.apply_question_policy({'ready_for_brief': False,
                    'question_field': field, 'question': bundled}, p)
                self.assertNotIn('parking', result['question'])
                self.assertNotIn('site/', result['question'])
                self.assertNotEqual(result['question'], bundled)

    def test_site_detail_alias_cannot_reopen_answered_or_deferred_site_group(self):
        for value, asked in (('no', []), ('yes', []), ('not_sure', []), ('unknown', ['site_work'])):
            with self.subTest(value=value, asked=asked):
                p = project(plumbing='unknown', site_work=value)
                p['intake_asked_fields'] = asked
                result = app.apply_question_policy({'ready_for_brief': False,
                    'question_field': 'site_changes', 'question': 'Any parking changes?'}, p)
                self.assertEqual(result['question_field'], 'plumbing')
                self.assertNotIn('parking', result['question'])

    def test_equipment_and_site_questions_remain_in_their_own_groups(self):
        for field in ('specialized_review', 'site_work', 'right_of_way'):
            p = project()
            p['fields'][field]['value'] = 'unknown'
            result = app.apply_question_policy({'ready_for_brief': False,
                'question_field': field, 'question': 'Any interior, equipment, parking or driveway changes?'}, p)
            self.assertEqual(result['question_field'], field)
            self.assertNotIn('interior', result['question'])
            if field == 'specialized_review':
                self.assertNotIn('parking', result['question'])
            if field == 'site_work':
                self.assertNotIn('equipment', result['question'])

    def test_business_use_checks_previous_use_before_interior_work(self):
        p = project(business_use='yes', existing_use='unknown', interior_work='unknown')
        result = app.apply_question_policy({'ready_for_brief': True}, p)
        self.assertEqual(result['question_field'], 'existing_use')
        self.assertIn('Certificate of Use', result['question_reason'])

    def test_ordered_checklist_ignores_model_attempt_to_jump_to_parking(self):
        p = project(interior_work='unknown', plumbing='unknown', specialized_review='unknown',
                    site_work='unknown', right_of_way='unknown')
        expected = ['interior_work', 'plumbing', 'specialized_review', 'site_work', 'right_of_way']
        for field in expected:
            result = app.apply_question_policy({'ready_for_brief': False,
                'question_field': 'site_work', 'question': 'Any parking changes?'}, p)
            self.assertEqual(result['question_field'], field)
            p = update_project(p, {field: {'value': 'no', 'evidence': 'no'}}, 'no')
        self.assertTrue(app.apply_question_policy({'ready_for_brief': False}, p)['ready_for_brief'])

    def test_short_answers_update_pending_field_and_multi_answer_skips_resolved_items(self):
        p = project(business_use='yes', existing_use='unused space', interior_work='unknown',
                    plumbing='unknown', specialized_review='unknown', site_work='unknown', right_of_way='unknown')
        p['pending_intake_field'] = 'interior_work'
        p['intake_asked_fields'] = ['interior_work']
        context = {'property': {}, 'sources': [], 'fees': []}
        for statement, updates, expected in (
            ('yes', {}, 'plumbing'),
            ('yes', {}, 'specialized_review'),
            ('yes', {}, 'site_work'),
            ('No work outside, including no driveway or sidewalk changes', {
                'site_work': {'value': 'no', 'evidence': 'No work outside'},
                'right_of_way': {'value': 'no', 'evidence': 'no driveway or sidewalk changes'}}, ''),
        ):
            with patch.object(app, 'OPENAI_API_KEY', 'test'), patch.object(app, 'research_context', return_value=context), patch.object(
                    app, 'complete_json', side_effect=[{'updates': updates}, {'ready_for_brief': False,
                    'message': 'Your answer is recorded.', 'question_field': 'proposed_use',
                    'question': 'What will the space be used for?'}]):
                result = app.openai_chat([{'role': 'user', 'text': statement}], p)
            self.assertEqual(result['question_field'], expected)
            p = result['project']
            if expected:
                self.assertEqual(p['pending_intake_field'], expected)
        self.assertEqual(result['stage'], 'final')
        self.assertTrue(result['brief']['action_plan'])
        self.assertNotIn('pending_intake_field', p)
        self.assertNotIn('right_of_way', p['intake_asked_fields'])
        self.assertEqual(p['fields']['interior_work']['value'], 'yes')
        self.assertEqual(p['fields']['plumbing']['value'], 'yes')
        self.assertEqual(p['fields']['specialized_review']['value'], 'yes')
        row_step = next(step for step in result['brief']['action_plan'] if step['id'] == 'row')
        self.assertEqual(row_step['status'], 'not_applicable')

    def test_bare_yes_cannot_fill_other_checklist_items(self):
        p = project(plumbing='unknown', site_work='unknown', right_of_way='unknown')
        p['pending_intake_field'] = 'plumbing'
        p['intake_asked_fields'] = ['plumbing']
        with patch.object(app, 'OPENAI_API_KEY', 'test'), patch.object(app, 'research_context', return_value={
                'property': {}, 'sources': [], 'fees': []}), patch.object(app, 'complete_json', side_effect=[
                {'updates': {'site_work': {'value': 'yes', 'evidence': 'yes'}}},
                {'ready_for_brief': True, 'message': 'Your answer is recorded.'}]):
            result = app.openai_chat([{'role': 'user', 'text': 'yes'}], p)
        self.assertEqual(result['project']['fields']['plumbing']['value'], 'yes')
        self.assertEqual(result['project']['fields']['site_work']['value'], 'unknown')
        self.assertEqual(result['question_field'], 'site_work')

    def test_exact_sink_reply_saves_plumbing_even_when_model_omits_or_mislabels_it(self):
        statement = 'yes i will add sinks and plaumbing for the sink not restrooms i wiull use local office restroos'
        for change in (None, {'value': 'unknown', 'evidence': statement},
                       {'value': 'adding sink plumbing', 'evidence': statement},
                       {'value': 'yes', 'evidence': 'I will add sinks'},
                       {'value': 'no', 'evidence': 'not restrooms'}):
            with self.subTest(change=change):
                p = project(plumbing='unknown')
                p['pending_intake_field'] = 'plumbing'
                p['intake_asked_fields'] = ['plumbing']
                updates = {'alterations': {'value': 'new sinks; existing office restrooms', 'evidence': statement}}
                if change is not None:
                    updates['plumbing'] = change
                with patch.object(app, 'OPENAI_API_KEY', 'test'), patch.object(app, 'research_context', return_value={
                        'property': {}, 'sources': [], 'fees': []}), patch.object(app, 'complete_json', side_effect=[
                        {'updates': updates}, {'ready_for_brief': True, 'message': 'The sink work is recorded.'}]):
                    result = app.workspace_result(app.openai_chat([{'role': 'user', 'text': statement}], p))
                self.assertEqual(result['project']['fields']['plumbing']['value'], 'yes')
                self.assertEqual(result['project']['fields']['alterations']['value'], 'new sinks; existing office restrooms')
                step = next(step for step in result['brief']['action_plan'] if step['id'] == 'plumbing')
                self.assertEqual(step['status'], 'applicable')
                self.assertIn(statement, step['evidence'])

    def test_conditional_or_reversed_yes_is_not_forced_to_plumbing_yes(self):
        for statement in ('yes if the City requires it', 'yes but no plumbing',
                          'yes i will not add sinks', 'yes maybe', 'could I add sinks?',
                          'the office already has sinks'):
            with self.subTest(statement=statement):
                self.assertEqual(app.pending_scope_answer(statement), '')

    def test_existing_session_recovers_missed_sink_answer_and_rebuilds_checklist(self):
        statement = 'yes i will add sinks and plaumbing for the sink not restrooms i wiull use local office restroos'
        p = project(plumbing='unknown')
        p['intake_asked_fields'] = ['plumbing']
        plumbing_question = next(question for field, question, _ in app.INTAKE_SCOPE_QUESTIONS if field == 'plumbing')
        record = {'project': p, 'history': [
            {'role': 'model', 'text': plumbing_question}, {'role': 'user', 'text': statement},
            {'role': 'model', 'text': 'Your preliminary checklist is ready.'}], 'result': None}
        state = demo_session.pack(record, 'test')
        with patch.dict(os.environ, {'PROJECT_STORAGE_MODE': 'demo'}), patch.object(app, 'OPENAI_API_KEY', 'test'), patch.object(
                app, 'research_context', return_value={'property': {}, 'sources': [], 'fees': []}), patch.object(
                app, 'complete_json', side_effect=[{'updates': {}},
                {'ready_for_brief': True, 'message': 'The checklist reflects your reported sink work.'}]):
            result = app.chat_request({'state': state, 'message': 'Update my checklist'})
        self.assertEqual(result['stage'], 'final')
        for plan in (result['brief'], result['live_plan']):
            step = next(step for step in plan['action_plan'] if step['id'] == 'plumbing')
            self.assertEqual(step['status'], 'applicable')
            self.assertIn(statement, step['evidence'])
        saved = demo_session.unpack(result['state'], 'test')
        self.assertEqual(saved['project']['fields']['plumbing']['value'], 'yes')

    def test_recovery_preserves_known_answers_and_real_conflicts(self):
        plumbing_question = next(question for field, question, _ in app.INTAKE_SCOPE_QUESTIONS if field == 'plumbing')
        history = [{'role': 'model', 'text': plumbing_question}, {'role': 'user', 'text': 'yes i will add sinks'}]
        for value in ('yes', 'no', 'not_sure'):
            p = project(plumbing=value)
            self.assertEqual(app.recover_unrecorded_answers(p, history), p)
        p = project(plumbing='unknown')
        p['conflicts']['plumbing'] = {'previous': 'yes', 'proposed': 'no', 'evidence': 'no plumbing'}
        self.assertEqual(app.recover_unrecorded_answers(p, history), p)

    def test_model_message_cannot_add_another_question_to_valid_followup(self):
        result = app.apply_question_policy({'ready_for_brief': False,
            'question_field': 'plumbing', 'question': 'Any new sinks?',
            'message': 'Will parking change? Do you plan interior work?'}, project(plumbing='unknown'))
        self.assertNotIn('?', result['message'])
        self.assertEqual(result['question'].count('?'), 1)

    def test_chat_renders_one_group_and_records_only_the_question_it_asked(self):
        p = project(interior_work='unknown', plumbing='unknown', site_work='unknown')
        with patch.object(app, 'OPENAI_API_KEY', 'test'), patch.object(app, 'research_context', return_value={
                'property': {}, 'sources': [], 'fees': []}), patch.object(app, 'complete_json', side_effect=[
                {'updates': {}}, {'ready_for_brief': False, 'question_field': 'interior_work',
                 'message': 'Will parking change?',
                 'question': 'Any interior renovations, plumbing changes, or site/parking alterations?'}]):
            result = app.openai_chat([{'role': 'user', 'text': 'I am still deciding'}], p)
        self.assertEqual(result['message'].count('?'), 1)
        self.assertNotIn('parking', result['message'])
        self.assertEqual(result['question_field'], 'interior_work')
        self.assertEqual(result['project']['intake_asked_fields'], ['interior_work'])

    def test_repeated_factual_question_does_not_stop_remaining_scope_questions(self):
        p = project(plumbing='unknown')
        p['intake_asked_fields'] = ['existing_use']
        question = {'ready_for_brief': False, 'question_field': 'existing_use', 'question': 'What was the previous use?'}
        self.assertEqual(app.apply_question_policy(question, p)['question_field'], 'plumbing')

    def test_real_conflicts_still_require_clarification(self):
        p = project()
        p['conflicts']['plumbing'] = {'previous': 'yes', 'proposed': 'no', 'evidence': 'no plumbing'}
        result = app.apply_question_policy(cou_offer(), p)
        self.assertFalse(result['ready_for_brief'])
        self.assertEqual(result['question_field'], 'plumbing')

    def test_missing_location_still_blocks_completion(self):
        result = app.apply_question_policy(cou_offer(), project(location='unknown'))
        self.assertFalse(result['ready_for_brief'])
        self.assertEqual(result['question_field'], 'location')

    def test_a_direct_answer_is_kept_when_finishing(self):
        result = cou_offer()
        result['message'] = 'Bring your project description, layout and any existing approval records to the meeting.'
        completed = app.apply_question_policy(result, project())
        self.assertEqual(completed['message'], result['message'])
        self.assertTrue(completed['ready_for_brief'])

    def test_api_returns_actual_checklist_instead_of_another_cou_question(self):
        p = project(right_of_way='unknown')
        record = {'project': p, 'history': [], 'result': None}
        state = demo_session.pack(record, 'test')
        with patch.dict(os.environ, {'PROJECT_STORAGE_MODE': 'demo'}), patch.object(app, 'OPENAI_API_KEY', 'test'), patch.object(
                app, 'research_context', return_value={'property': {}, 'sources': [], 'fees': []}), patch.object(
                app, 'complete_json', side_effect=[{'updates': {'right_of_way': {'value': 'not_sure', 'evidence': 'not really'}}}, cou_offer()]):
            result = app.chat_request({'state': state, 'message': 'not really'})
        self.assertEqual(result['stage'], 'final')
        self.assertTrue(result['brief']['action_plan'])
        self.assertEqual(result['question_field'], '')
        self.assertNotIn('Have you confirmed', result['message'])
        self.assertEqual(next(step for step in result['brief']['action_plan'] if step['id'] == 'row')['status'], 'unresolved')
        restored = demo_session.unpack(result['state'], 'test')
        self.assertEqual(restored['project']['fields']['right_of_way']['value'], 'not_sure')


if __name__ == '__main__':
    unittest.main()
