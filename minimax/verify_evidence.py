"""Verify the delivered evidence linkage without an API key or network."""
import json
from pathlib import Path

from sdd_context.documents import digest
from sdd_context.workflow import version


def verify(root: Path):
    def read(name):
        return json.loads((root / name).read_text(encoding='utf-8'))

    before_version, before_hashes = version(root / 'before')
    after_version, after_hashes = version(root / 'after')
    before, after = read('before-tests.json'), read('after-tests.json')
    assert before['source_hashes'] == before_hashes
    assert after['source_hashes'] == after_hashes
    assert {k:v for k,v in before_hashes.items() if k != 'tickets.py'} == {
        k:v for k,v in after_hashes.items() if k != 'tickets.py'}
    for phase, tests, code_version in (('before', before, before_version), ('after', after, after_version)):
        for suite, expected_count in (('target', 7), ('regression', 12)):
            result = tests['results'][suite]
            assert result['code_version'] == code_version
            assert result['executed_tests'] == expected_count
            assert not result['environment_errors']
            log = (root / result['log']).read_text(encoding='utf-8')
            assert f'Ran {expected_count} tests' in log
            if phase == 'before' and suite == 'target':
                assert result['exit_code'] != 0 and result['assertion_failures'] == 7
                assert 'AssertionError' in log
            else:
                assert result['exit_code'] == 0 and result['suite_ok']
                assert '\nOK\n' in log
    record = read('minimax-record.json')
    assert record['mode'] == 'live-model-request'
    assert record['response']['id'] and record['response']['finish_reason'] == 'stop'
    assert record['response']['usage']['completion_tokens'] > 0
    assert record['before_source_sha256'] == before_hashes['tickets.py']
    assert record['result']['path'] == 'tickets.py'
    assert digest(record['result']['content']) == after_hashes['tickets.py']
    request = json.loads(record['request']['body']['messages'][1]['content'])
    assert request['task'] == read('task.json')
    assert digest(request['source_text']) == before_hashes['tickets.py']
    assert request['retrieved_context'] == read('context-resumed.json')
    versions = []
    for phase in ('v1', 'v2'):
        context = read(f'context-{phase}.json')
        assert context['sql'].startswith('SELECT') and context['parameters']
        docs = {d['path']: d for d in context['documents']}
        assert set(request['task']['required_sources']) <= docs.keys()
        assert 'specs/payment_receipts.md' not in docs
        versions.append(docs['specs/ticket_filters.md'])
    assert 'tag_match_mode: ANY' in versions[0]['content']
    assert 'tag_match_mode: ALL' in versions[1]['content']
    assert versions[1]['revision'] > versions[0]['revision']
    assert versions[1]['sha256'] == before_hashes['specs/ticket_filters.md']
    for phase, code_version in (('before', before_version), ('after', after_version)):
        saved, restored = read(f'state-saved-{phase}.json'), read(f'state-restored-{phase}.json')
        assert saved['writer_pid'] != restored['pid']
        assert saved['state'] == restored['state']
        assert saved['state']['code_version'] == code_version
        assert saved['evidence']['backend'] == 'powercontext'
    assert read('state-restored-after.json')['state']['pending'] == []
    assert [item['id'] for item in read('after-cli.json')['output']] == [9]
    return {'verified': True, 'target_before': '7 assertion failures', 'target_after': '7 passed',
            'regression_before_after': '12 passed', 'unchanged_tests': True,
            'model': record['response']['model'], 'after_code_version': after_version}


if __name__ == '__main__':
    print(json.dumps(verify(Path(__file__).resolve().parent / 'evidence'), indent=2))
