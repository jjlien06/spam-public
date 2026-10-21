import json
from dataclasses import asdict

import pytest

from job_agent.agent_client import AgentClient, AgentConfig, AgentError, cli_command, save_config, usage
from job_agent.agent_answers import suggest
from test_answer_deck import setup_deck, add_question


def test_luna_is_explicit_and_has_no_shell_or_model_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr('job_agent.agent_client.shutil.which', lambda _: '/bin/codex')
    command = cli_command(AgentConfig(), tmp_path, {})
    assert command[command.index('--model') + 1] == 'gpt-6-luna'
    assert '--ignore-user-config' in command and '--ephemeral' in command
    assert 'shell_tool' in command and 'model_reasoning_effort="low"' in command
    with pytest.raises(AgentError):
        AgentConfig(model='gpt-6-astra').validate()


def test_cache_budget_and_failures_do_not_start_paid_retry_loops(tmp_path):
    calls = []
    def transport(config, prompt, schema):
        calls.append(prompt)
        return {'ready': True}, {'input_tokens': 20, 'output_tokens': 5}
    client = AgentClient(tmp_path, AgentConfig(enabled=True, max_calls_per_run=1), transport)
    validate = lambda result: None
    assert not client.request('check', {}, {}, validate)['cached']
    assert client.request('check', {}, {}, validate)['cached']
    with pytest.raises(AgentError, match='budget'):
        client.request('other', {}, {}, validate)
    assert len(calls) == 1 and usage(tmp_path)['input_tokens'] == 20
    def fail(*args):
        raise AgentError('failed')
    client = AgentClient(tmp_path, AgentConfig(enabled=True), fail)
    with pytest.raises(AgentError, match='failed'):
        client.request('failure', {}, {}, validate)
    with pytest.raises(AgentError, match='already failed'):
        client.request('failure', {}, {}, validate)
    assert usage(tmp_path)['calls'] == 2


def test_unknown_question_suggestion_is_sourced_cached_and_never_saved(tmp_path):
    deck, queue, profile = setup_deck(tmp_path, {'answers': {'Preferred location': 'Remote'}})
    add_question(queue, tmp_path, 'a', 'Which work location do you prefer?', ['Remote', 'Office'])
    before = profile.read_bytes()
    def transport(config, prompt, schema):
        data = json.loads(prompt)['data']
        assert 'accounts' not in prompt
        return {'answers': [{'id': data['questions'][0]['id'], 'answer': 'Remote',
                 'sources': [data['sources'][0]['id']], 'reason': 'Saved location preference'}]}, {}
    client = AgentClient(tmp_path, AgentConfig(enabled=True), transport)
    result = suggest(deck, client=client)
    assert result['proposals'][0]['answer'] == 'Remote'
    assert profile.read_bytes() == before
    assert suggest(deck, client=client)['proposals'][0]['cached']


def test_missing_personal_fact_does_not_call_model(tmp_path):
    deck, queue, profile = setup_deck(tmp_path)
    add_question(queue, tmp_path, 'a', 'Do you know an employee here?', ['Yes', 'No'])
    def never(*args):
        raise AssertionError('No evidence must not invoke a model')
    result = suggest(deck, client=AgentClient(tmp_path, AgentConfig(enabled=True), never))
    assert result['calls'] == 0 and result['proposals'][0]['answer'] is None


def test_fabricated_source_or_nonexistent_option_is_rejected(tmp_path):
    deck, queue, profile = setup_deck(tmp_path, {'answers': {'Preferred location': 'Remote'}})
    add_question(queue, tmp_path, 'a', 'Preferred work location?', ['Remote', 'Office'])
    def transport(config, prompt, schema):
        data=json.loads(prompt)['data']
        return {'answers':[{'id':data['questions'][0]['id'],'answer':'Mars','sources':['made-up'],'reason':'guess'}]}, {}
    with pytest.raises(AgentError):
        suggest(deck,client=AgentClient(tmp_path,AgentConfig(enabled=True),transport))
    assert 'Preferred work location?' not in json.loads(profile.read_text())['answers']
