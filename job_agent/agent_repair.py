"""Bounded function-edit proposals. No model shell, no automatic code execution."""
import ast
import difflib
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .agent_client import AgentClient, AgentError, digest, private_json, operation_lock

# These isolated helpers cannot alter submit, queue, account, or policy code.
ALLOWED = {
    'job_agent/custom_controls.py': {'option_match'},
    'job_agent/repeated_sections.py': {'search_key', '_wanted'},
    'job_agent/questions.py': {'question_label'},
}
SCHEMA = {'type':'object','additionalProperties':False,'required':['replacement','reason'],
          'properties':{'replacement':{'type':['string','null']},'reason':{'type':'string'}}}


def propose(root, state, path, function, diagnostic, client=None):
    root, state = Path(root).resolve(), Path(state)
    if function not in ALLOWED.get(path, set()):
        raise AgentError('This function is outside the bounded repair allowlist. Use a normal reviewed coding session.')
    if len(diagnostic) > 1800 or not diagnostic.strip():
        raise AgentError('Provide a redacted diagnostic of 1–1800 characters.')
    source_path = root / path
    source = source_path.read_text()
    tree = ast.parse(source)
    node = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function), None)
    if node is None or node.decorator_list:
        raise AgentError('Expected one undecorated function.')
    lines = source.splitlines(keepends=True)
    fragment = ''.join(lines[node.lineno-1:node.end_lineno])
    payload = {'function':fragment,'diagnostic':diagnostic,
               'instruction':'Propose a minimal replacement for this function only, or null when more context is needed. Preserve the signature and behavior outside the reported bug. No imports, file/network/process access, dynamic execution, or policy changes. Return reason and replacement.'}
    def validate(result):
        if not isinstance(result,dict) or set(result)!={'replacement','reason'} or not isinstance(result['reason'],str):
            raise AgentError('Invalid repair response.')
        replacement=result['replacement']
        if replacement is None:
            return
        if not isinstance(replacement,str) or len(replacement)>12000:
            raise AgentError('Repair exceeds the function-size limit.')
        parsed=ast.parse(replacement)
        if len(parsed.body)!=1 or not isinstance(parsed.body[0],ast.FunctionDef):
            raise AgentError('Repair must contain exactly one function.')
        new=parsed.body[0]
        if new.name!=function or new.decorator_list or ast.dump(new.args)!=ast.dump(node.args):
            raise AgentError('Repair changed the function signature.')
        forbidden={'eval','exec','compile','open','__import__','getattr','setattr','globals','locals','breakpoint'}
        for item in ast.walk(new):
            if isinstance(item,(ast.Import,ast.ImportFrom,ast.Global,ast.Nonlocal)):
                raise AgentError('Repair introduced forbidden scope or imports.')
            if isinstance(item,ast.Name) and (item.id in forbidden or item.id.startswith('__')):
                raise AgentError('Repair introduced forbidden dynamic access.')
            if isinstance(item,ast.Attribute) and item.attr.startswith('__'):
                raise AgentError('Repair introduced forbidden dynamic access.')
        ast.parse(''.join(lines[:node.lineno-1])+replacement+'\n'+''.join(lines[node.end_lineno:]))
    response=(client or AgentClient(state)).request('repair_function',payload,SCHEMA,validate)
    result=response['result']
    proposal={'path':path,'function':function,'source_hash':digest(source),'reason':result['reason'],
              'replacement':result['replacement'],'status':'needs_review','cached':response['cached']}
    key=digest(proposal)[:16]
    directory=state/'agent'/'repairs'/key
    private_json(directory/'proposal.json',proposal)
    if result['replacement'] is not None:
        updated=''.join(lines[:node.lineno-1])+result['replacement'].rstrip()+'\n'+''.join(lines[node.end_lineno:])
        patch=''.join(difflib.unified_diff(source.splitlines(True),updated.splitlines(True),fromfile='a/'+path,tofile='b/'+path))
        patch_path=directory/'proposal.patch'
        patch_path.write_text(patch)
        patch_path.chmod(0o600)
    return {'id':key,'status':'needs_review','proposal':str(directory/'proposal.json'),
            'patch':str(directory/'proposal.patch') if result['replacement'] else None,
            'reason':result['reason'],'cached':response['cached']}


def _proposal(state, key):
    if not re.fullmatch(r'[0-9a-f]{16}', key):
        raise AgentError('Invalid repair ID.')
    directory=Path(state).resolve()/'agent'/'repairs'/key
    proposal=json.loads((directory/'proposal.json').read_text())
    if proposal['function'] not in ALLOWED.get(proposal['path'],set()):
        raise AgentError('Repair is outside the allowlist.')
    return directory,proposal


def check(root,state,key,tests):
    """Check a proposal in a detached checkout with private files and network denied."""
    root=Path(root).resolve()
    directory,proposal=_proposal(state,key)
    if sys.platform!='darwin' or not shutil.which('sandbox-exec'):
        raise AgentError('Automatic repair tests currently require macOS sandbox-exec; review the patch manually on other systems.')
    if not tests or any(not re.fullmatch(r'tests/test_[a-z0-9_]+\.py(?:::[A-Za-z0-9_]+)?',t) for t in tests):
        raise AgentError('Select existing test files or individual tests under tests/.')
    if digest((root/proposal['path']).read_text())!=proposal['source_hash']:
        raise AgentError('Source changed; generate a fresh repair.')
    stats=subprocess.run(['git','apply','--numstat',str(directory/'proposal.patch')],cwd=root,check=True,capture_output=True,text=True).stdout.splitlines()
    if len(stats)!=1 or stats[0].split('\t')[-1]!=proposal['path']:
        raise AgentError('A repair may modify only its selected allowlisted source file.')
    if subprocess.run(['git','diff','--quiet','HEAD','--',proposal['path']],cwd=root).returncode:
        raise AgentError('Commit the source baseline before checking this repair.')
    worktree=directory/'checkout'
    with operation_lock(state,'applications'):
        if worktree.exists():
            raise AgentError('This repair already has a checkout; inspect its recorded result.')
        subprocess.run(['git','worktree','add','--detach',str(worktree),'HEAD'],cwd=root,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            subprocess.run(['git','apply','--check',str(directory/'proposal.patch')],cwd=worktree,check=True,capture_output=True)
            subprocess.run(['git','apply',str(directory/'proposal.patch')],cwd=worktree,check=True,capture_output=True)
            for test in tests:
                if not (worktree/test.split('::')[0]).is_file():
                    raise AgentError('Selected test is not part of the committed baseline.')
            home=str(Path.home().resolve())
            venv=str((root/'.venv').resolve())
            with tempfile.TemporaryDirectory(prefix='spam-repair-test-') as temp:
                sandbox='(version 1)\n(allow default)\n(deny network*)\n'
                sandbox+='(deny file-read* (subpath '+json.dumps(home)+'))\n'
                sandbox+='(allow file-read* (subpath '+json.dumps(str(worktree))+') (subpath '+json.dumps(venv)+'))\n'
                sandbox+='(allow file-read-metadata)\n'
                sandbox+='(deny file-write*)\n(allow file-write* (subpath '+json.dumps(temp)+') (subpath '+json.dumps(str(worktree))+') (literal "/dev/null"))\n'
                command=['sandbox-exec','-p',sandbox,str(root/'.venv/bin/python'),'-m','pytest','-q',*tests,'--tb=short','-p','no:cacheprovider']
                env={'PATH':'/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin','HOME':temp,'TMPDIR':temp,
                     'PYTHONDONTWRITEBYTECODE':'1','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1',
                     'PLAYWRIGHT_BROWSERS_PATH':str(Path.home()/'Library/Caches/ms-playwright')}
                # Chromium binaries are read-only; no saved browser profile is exposed.
                sandbox+='(allow file-read* (subpath '+json.dumps(env['PLAYWRIGHT_BROWSERS_PATH'])+'))\n'
                command[2]=sandbox
                run=subprocess.run(command,cwd=worktree,env=env,capture_output=True,timeout=300)
                log=directory/'tests.log';log.write_bytes(run.stdout+run.stderr);log.chmod(0o600)
                result={'passed':run.returncode==0,'tests':tests,'patch_hash':digest((directory/'proposal.patch').read_text()),
                        'source_hash':proposal['source_hash']}
                private_json(directory/'test-result.json',result)
                return {**result,'log':str(log)}
        finally:
            subprocess.run(['git','worktree','remove','--force',str(worktree)],cwd=root,capture_output=True)


def apply(root,state,key):
    root=Path(root).resolve()
    directory,proposal=_proposal(state,key)
    result=json.loads((directory/'test-result.json').read_text())
    patch=(directory/'proposal.patch').read_text()
    if not result.get('passed') or result['patch_hash']!=digest(patch):
        raise AgentError('The exact patch must pass its fixture checks before applying.')
    if digest((root/proposal['path']).read_text())!=proposal['source_hash']:
        raise AgentError('Source changed since the proposal; do not apply a stale repair.')
    with operation_lock(state,'applications'):
        subprocess.run(['git','apply','--check',str(directory/'proposal.patch')],cwd=root,check=True,capture_output=True)
        subprocess.run(['git','apply',str(directory/'proposal.patch')],cwd=root,check=True,capture_output=True)
    return {'applied':key,'path':proposal['path'],'committed':False}
