import json
import subprocess
import sys
from pathlib import Path

import pytest

from job_agent.agent_client import AgentClient, AgentConfig, AgentError
from job_agent.agent_repair import propose


def test_repair_returns_reviewable_patch_without_changing_code(tmp_path):
    root=tmp_path/'repo';(root/'job_agent').mkdir(parents=True)
    path=root/'job_agent/questions.py';original='def question_label(value):\n    return value\n'
    path.write_text(original)
    def transport(*args):
        return {'replacement':'def question_label(value):\n    return value.strip()\n','reason':'Strip surrounding whitespace.'},{}
    state=tmp_path/'state'
    result=propose(root,state,'job_agent/questions.py','question_label','Leading whitespace is retained.',
                   AgentClient(state,AgentConfig(enabled=True),transport))
    assert path.read_text()==original
    assert '+    return value.strip()' in Path(result['patch']).read_text()
    assert result['status']=='needs_review'


@pytest.mark.parametrize('replacement',[
    'def question_label(value):\n    import os\n    return value',
    'def question_label(value):\n    return open("profile.json").read()',
    'def different(value):\n    return value',
    'def question_label(value, extra):\n    return value',
])
def test_repair_rejects_unsafe_or_unrelated_code(tmp_path,replacement):
    (tmp_path/'job_agent').mkdir()
    (tmp_path/'job_agent/questions.py').write_text('def question_label(value):\n    return value\n')
    state=tmp_path/'state'
    def transport(*args):return {'replacement':replacement,'reason':'change'},{}
    with pytest.raises(AgentError):
        propose(tmp_path,state,'job_agent/questions.py','question_label','problem',AgentClient(state,AgentConfig(enabled=True),transport))


def test_repair_cannot_modify_submission_code(tmp_path):
    with pytest.raises(AgentError,match='allowlist'):
        propose(tmp_path,tmp_path/'state','job_agent/runner.py','submit_application','problem')


@pytest.mark.skipif(sys.platform!='darwin',reason='macOS sandbox integration')
def test_repair_checkout_tests_and_reviewed_apply(tmp_path):
    from job_agent.agent_repair import check, apply
    root=tmp_path/'repo';(root/'job_agent').mkdir(parents=True);(root/'tests').mkdir()
    (root/'job_agent/__init__.py').write_text('')
    source=root/'job_agent/questions.py';source.write_text('def question_label(value):\n    return value\n')
    (root/'tests/test_question.py').write_text('from job_agent.questions import question_label\ndef test_trim():\n    assert question_label(" a ")=="a"\n')
    (root/'.venv').symlink_to(Path(__file__).resolve().parents[1]/'.venv',target_is_directory=True)
    for command in [['git','init','-q'],['git','add','job_agent','tests'],['git','-c','user.name=Fixture','-c','user.email=fixture@example.test','commit','-qm','fixture']]:
        subprocess.run(command,cwd=root,check=True)
    state=tmp_path/'state'
    def transport(*args):return {'replacement':'def question_label(value):\n    return value.strip()\n','reason':'fixture'},{}
    result=propose(root,state,'job_agent/questions.py','question_label','whitespace',AgentClient(state,AgentConfig(enabled=True),transport))
    checked=check(root,state,result['id'],['tests/test_question.py'])
    assert checked['passed'],Path(checked['log']).read_text()
    assert 'strip' not in source.read_text()
    assert apply(root,state,result['id'])['applied']==result['id']
    assert 'strip' in source.read_text()
