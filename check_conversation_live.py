"""Opt-in live replay: uses configured AI provider and public City research.

Run manually with python check_conversation_live.py; never part of unit discovery.
"""
import json

import app
from project_workflow import empty_project


def main():
    project = empty_project()
    history = []
    for statement in ('reconstruction Philips Highway Warehouse', '6100 philips hwy',
                      'check all 3 parcels', 'continue', 'a new building with sinks',
                      'none', 'gradining and parking\\', 'nbo'):
        history.append({'role': 'user', 'text': statement})
        result = app.openai_chat(history, project)
        project = result['project']
        print(json.dumps({'user': statement, 'reply': result['message'],
                          'question_field': result['question_field'],
                          'pending': project.get('pending_intake_field'),
                          'intent': project.get('conversation', {}).get('turn_intent')}, ensure_ascii=True), flush=True)
        history.append({'role': 'model', 'text': result['message']})
        if statement == '6100 philips hwy':
            pending_before_research = project.get('pending_intake_field')
        if statement == 'check all 3 parcels':
            assert result['stage'] == 'follow_up' and result['question_field'] == ''
            assert project.get('pending_intake_field') == pending_before_research
            assert project['fields']['plumbing']['value'] == 'unknown'
        if statement == 'continue':
            assert result['question_field'] == pending_before_research
        if statement == 'a new building with sinks':
            assert project['fields']['project_type']['value'] == 'new_construction'
            assert project['fields']['plumbing']['value'] == 'yes'
            assert result['question_field'] not in {'project_type', 'interior_work', 'plumbing', 'existing_use'}
        if statement == 'gradining and parking\\':
            assert project['fields']['site_work']['value'] == 'yes'
            assert result['question_field'] == 'right_of_way'
        if statement == 'nbo':
            assert project['fields']['right_of_way']['value'] == 'no'
            assert project['fields']['site_work']['value'] == 'yes'
            assert result['stage'] == 'final'
    print('Live conversation replay passed.', flush=True)


if __name__ == '__main__':
    main()
