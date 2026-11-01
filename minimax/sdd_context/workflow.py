"""Inspectable SDD workflow. Every invocation opens and closes its own state runtime."""
from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT
from .documents import DocumentStore, digest
from .minimax_client import generate_change
from .state import StateStore


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def version(root):
    files = {p.relative_to(root).as_posix(): digest(p.read_text(encoding='utf-8'))
             for p in sorted(root.rglob('*')) if p.suffix in ('.py', '.md', '.json')}
    return digest(json.dumps(files, sort_keys=True)), files


def run_tests(run: Path, stage: str, task: dict):
    work = run / 'work'
    code_version, hashes = version(work)
    results = {}
    for suite in ('target', 'regression'):
        command = [sys.executable, *task['commands'][suite][1:]]
        process = subprocess.run(command, cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 encoding='utf-8', errors='replace', timeout=60,
                                 env={**{k: v for k, v in os.environ.items() if k.upper() in
                                       {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'HOME', 'LANG', 'LC_ALL'}},
                                      'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'})
        output = process.stdout.replace(str(work), '$SAMPLE')
        log = run / f'{stage}-{suite}.log'
        log.write_text(output, encoding='utf-8')
        results[suite] = {'command': ['python', *command[1:]], 'cwd': 'work',
                          'exit_code': process.returncode, 'code_version': code_version,
                          'log': log.name, 'assertion_failures': output.count('AssertionError'),
                          'environment_errors': 'ERROR:' in output or 'ImportError' in output,
                          'executed_tests': int(re.search(r'Ran (\d+) tests?', output).group(1)) if re.search(r'Ran (\d+) tests?', output) else 0,
                          'suite_ok': bool(re.search(r'^OK\s*$', output, re.MULTILINE)),
                          'timestamp_utc': datetime.now(timezone.utc).isoformat()}
    write_json(run / f'{stage}-tests.json', {'results': results, 'source_hashes': hashes})
    if results['target']['executed_tests'] != 7 or results['regression']['executed_tests'] != 12:
        raise RuntimeError('Expected 7 target and 12 regression tests to actually execute')
    return results


def context_for(store, task, run, phase, state=None):
    result = store.retrieve(task['project_id'], task['query'])
    selected = {doc['path']: doc for doc in result['documents']}
    missing = set(task['required_sources']) - selected.keys()
    if missing:
        raise RuntimeError('Retrieval missed necessary sources: ' + ','.join(sorted(missing)))
    for doc in selected.values():
        source = run / 'work' / doc['path']
        if not source.is_file() or digest(source.read_text(encoding='utf-8')) != doc['sha256']:
            raise RuntimeError('Retrieved source does not match current project')
    for symbol in task['required_symbols']:
        if not any(symbol in doc['content'] for doc in selected.values()):
            raise RuntimeError('Missing required specification or code symbol: ' + symbol)
    if any(path in selected for path in task['distractor_sources']):
        raise RuntimeError('Unrelated accounting document was selected')
    result['task_state'] = state
    write_json(run / f'context-{phase}.json', result)
    return result


def prepare(run: Path):
    if run.exists():
        raise ValueError('Run directory already exists; choose a new --run path')
    run.mkdir(parents=True)
    task = read_json(ROOT / 'fixtures/task.json')
    task['project_id'] = 'ticket-project-' + uuid.uuid4().hex[:16]
    write_json(run / 'task.json', task)
    shutil.copytree(ROOT / 'sample', run / 'work', ignore=shutil.ignore_patterns('__pycache__'))
    store = DocumentStore()
    identity = store.initialize()
    write_json(run / 'database.json', {'server': identity, 'database': store.database})
    write_json(run / 'import-v1.json', store.import_project(task['project_id'], run / 'work'))
    first = context_for(store, task, run, 'v1')
    before = next(doc for doc in first['documents'] if doc['path'] == task['spec_source'])
    if 'tag_match_mode: ANY' not in before['content']:
        raise RuntimeError('Initial specification is not v1')
    shutil.copyfile(ROOT / task['spec_update_file'], run / 'work' / task['spec_source'])
    write_json(run / 'import-v2.json', store.import_project(task['project_id'], run / 'work'))
    second = context_for(store, task, run, 'v2')
    after = next(doc for doc in second['documents'] if doc['path'] == task['spec_source'])
    if (after['sha256'] == before['sha256'] or after['revision'] <= before['revision']
            or 'tag_match_mode: ALL' not in after['content'] or 'tag_match_mode: ANY' in after['content']):
        raise RuntimeError('Reimport did not replace the old specification')
    shutil.copytree(run / 'work', run / 'before')
    tests = run_tests(run, 'before', task)
    if tests['target']['exit_code'] == 0 or tests['target']['assertion_failures'] == 0 or tests['target']['environment_errors']:
        raise RuntimeError('Baseline must fail due to target behavior, not environment')
    if tests['regression']['exit_code'] != 0:
        raise RuntimeError('Baseline regression tests failed')
    state = {'completed': ['import', 'spec-v2', 'baseline-tests'], 'pending': ['minimax-change', 'after-tests'],
             'test_results': tests, 'code_version': version(run / 'work')[0]}
    state_store = StateStore(run / 'state.db')
    if asyncio.run(state_store.load(task['task_id'])) is not None:
        raise RuntimeError('Expected an empty initial PowerContext state')
    saved = asyncio.run(state_store.save(task['task_id'], state))
    saved['writer_pid'] = os.getpid()
    write_json(run / 'state-saved-before.json', saved)
    return {'prepared': True, 'run': str(run), 'next': 'resume --generate (requires MiniMax key)'}


def resume(run: Path, *, generate=False, apply_candidate=False, replay: Path | None = None):
    task = read_json(run / 'task.json')
    store = StateStore(run / 'state.db')
    state = asyncio.run(store.load(task['task_id']))
    if state is None:
        raise RuntimeError('No persisted task found; run prepare first')
    if state['code_version'] != version(run / 'work')[0]:
        raise RuntimeError('Working code differs from saved task version; refusing stale resume')
    restored = {'pid': os.getpid(), 'state': state, 'operation': 'new process reads PowerContext memory'}
    write_json(run / ('state-restored-after.json' if 'after-tests' in state['completed'] else 'state-restored-before.json'), restored)
    # The restored completion state controls the action, rather than only being displayed.
    if 'after-tests' in state['completed']:
        return {'state_reused': True, 'action': 'already-validated; skip model modification', 'code_version': state['code_version']}
    context = context_for(DocumentStore(), task, run, 'resumed', state)
    if not generate and not apply_candidate and replay is None:
        return {'state_reused': True, 'action': 'continue at minimax-change', 'pending': state['pending']}
    source = run / 'work' / task['editable_file']
    old = source.read_text(encoding='utf-8')
    if generate:
        evidence = generate_change(task, context, old)
        evidence['mode'] = 'live-model-request'
        evidence['before_source_sha256'] = digest(old)
        write_json(run / 'minimax-record.json', evidence)
        (run / 'candidate.py').write_text(evidence['result']['content'], encoding='utf-8')
        return {'generated': True, 'next': 'Inspect candidate.py, then resume --apply', 'candidate': str(run / 'candidate.py')}
    elif apply_candidate:
        evidence = read_json(run / 'minimax-record.json')
        if evidence.get('mode') != 'live-model-request':
            raise ValueError('Apply requires a real MiniMax generation record')
    else:
        original = read_json(replay)
        if original.get('mode') != 'live-model-request':
            raise ValueError('Replay requires the delivered real model record')
        evidence = {**original, 'mode': 'replay-saved-model-change', 'original_record_sha256': digest(replay.read_text(encoding='utf-8'))}
        write_json(run / 'minimax-record.json', evidence)
    if evidence.get('before_source_sha256') != digest(old):
        raise RuntimeError('Model record was generated for a different baseline')
    supplied = json.loads(evidence['request']['body']['messages'][1]['content'])
    current_hashes = {doc['path']: doc['sha256'] for doc in context['documents']}
    supplied_hashes = {doc['path']: doc['sha256'] for doc in supplied['retrieved_context']['documents']}
    if ({k:v for k,v in supplied['task'].items() if k != 'project_id'} != {k:v for k,v in task.items() if k != 'project_id'}
            or supplied['source_text'] != old or current_hashes != supplied_hashes):
        raise RuntimeError('Recorded task/context does not match this run')
    result = evidence['result']
    if result['path'] != task['editable_file'] or result['path'] != 'tickets.py':
        raise ValueError('Model may only modify tickets.py')
    new = result['content']
    if old == new:
        raise RuntimeError('Model did not change the code')
    compile(new, 'tickets.py', 'exec')
    # Guard all non-editable inputs, including both immutable test suites.
    _, before_hashes = version(run / 'before')
    _, working_hashes = version(run / 'work')
    if before_hashes != working_hashes:
        raise RuntimeError('Non-editable input changed')
    source.write_text(new, encoding='utf-8')
    _, after_hashes = version(run / 'work')
    (run / 'change.diff').write_text(''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                 fromfile='before/tickets.py', tofile='after/tickets.py')), encoding='utf-8')
    docs = DocumentStore()
    try:
        tests = run_tests(run, 'after', task)
        if any(result['exit_code'] != 0 or not result['suite_ok'] for result in tests.values()):
            raise RuntimeError('Generated change failed tests; inspect after logs, no success recorded')
        _, tested_hashes = version(run / 'work')
        if tested_hashes != after_hashes:
            raise RuntimeError('Tests modified project inputs')
        cli_command = [sys.executable, *task['expected_after_cli']['argv'][1:]]
        cli = subprocess.run(cli_command, cwd=run / 'work', capture_output=True, text=True,
                             encoding='utf-8', timeout=20,
                             env={k:v for k,v in os.environ.items() if k.upper() in {'PATH','SYSTEMROOT','TEMP','TMP'}})
        if cli.returncode != 0 or [item['id'] for item in json.loads(cli.stdout)] != task['expected_after_cli']['ids']:
            raise RuntimeError('Modified CLI did not return expected ticket IDs')
        write_json(run / 'after-cli.json', {'command': task['expected_after_cli']['argv'],
                   'exit_code': cli.returncode, 'output': json.loads(cli.stdout), 'code_version': version(run / 'work')[0]})
        shutil.copytree(run / 'work', run / 'after', dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
        write_json(run / 'import-after.json', docs.import_project(task['project_id'], run / 'work'))
        saved = asyncio.run(store.save(task['task_id'], {'completed': [*state['completed'], 'minimax-change', 'after-tests'],
                'pending': [], 'test_results': {'before': state['test_results'], 'after': tests},
                'code_version': version(run / 'work')[0]}))
    except BaseException:
        # A state write may have committed just before an interrupted return.
        # Keep the validated version in that case; otherwise restore resumability.
        try:
            committed = asyncio.run(store.load(task['task_id']))
        except Exception:
            committed = None
        if committed is None or committed['code_version'] != version(run / 'work')[0]:
            source.write_text(old, encoding='utf-8')
            try:
                docs.import_project(task['project_id'], run / 'work')
            except Exception:
                pass  # A later import can recover a temporarily unavailable database.
        raise
    saved['writer_pid'] = os.getpid()
    write_json(run / 'state-saved-after.json', saved)
    return {'validated': True, 'code_version': saved['state']['code_version'], 'mode': evidence['mode']}


def export(run, output):
    if output.exists():
        raise ValueError('Export destination exists')
    if not (run / 'state-restored-after.json').is_file():
        raise RuntimeError('Must first run resume in a new process after success')
    output.mkdir(parents=True)
    for path in run.iterdir():
        if path.is_file() and path.suffix in ('.json', '.log', '.diff'):
            shutil.copyfile(path, output / path.name)
    for name in ('before', 'after'):
        shutil.copytree(run / name, output / name, ignore=shutil.ignore_patterns('__pycache__'))
    return {'exported': str(output)}
