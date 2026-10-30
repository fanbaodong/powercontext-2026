"""Independent host acceptance checks against real seekdb; no database mocks."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from app.main import app
from app.database import get_cursor
from app.services import document as document_service

records = []
projects = []
tag = uuid.uuid4().hex[:8]

def check(name, task):
    try:
        task()
        records.append({'check': name, 'passed': True})
        print('PASS:', name, flush=True)
    except Exception as exc:
        records.append({'check': name, 'passed': False, 'error': str(exc)[:600]})
        print('FAIL:', name, str(exc)[:600], flush=True)

def expect(condition, detail='Unexpected result'):
    if not condition:
        raise AssertionError(detail)

def ok(response):
    expect(response.is_success, f'HTTP {response.status_code}: {response.text[:350]}')
    return response.json()

with TestClient(app, base_url='http://127.0.0.1', raise_server_exceptions=False) as client:
    try:
        check('Real seekdb health', lambda: expect(ok(client.get('/api/health'))['status'] == 'ok'))
        for suffix in ('A', 'B'):
            projects.append(ok(client.post('/api/projects', json={'name': f'HOST-{tag}-{suffix}'}))['id'])
        pa, pb = projects
        payload = {'project_id': pa, 'title': '主机验证：原文与出处', 'author': '合成验证资料',
                   'source_url': 'https://example.com/notes', 'tags': '测试',
                   'content': '标题\r\n\r\n间隔重复：制定下次复习日期，保留每一次学习记录。\r\n\r\n<script>window.pwned=true</script>'}
        doc = ok(client.post('/api/documents', json=payload))
        chunks = ok(client.get(f'/api/documents/{doc["id"]}/chunks'))
        check('Short paragraphs and CRLF preserved', lambda: expect(len(chunks) == 3 and chunks[0]['content'] == '标题'))
        check('Same-project duplicate is idempotent', lambda: expect(ok(client.post('/api/documents', json=payload))['id'] == doc['id']))
        twin = ok(client.post('/api/documents', json={**payload, 'project_id': pb}))
        check('Cross-project copies are independent', lambda: expect(twin['id'] != doc['id']))
        result = ok(client.get(f'/api/search/project/{pa}', params={'q': '间隔重复'}))
        check('Long Chinese keyword uses actual FTS', lambda: expect(result['total'] > 0 and any('间隔重复' in r['content'] for r in result['results'])))
        check('FTS results stay in selected project', lambda: expect(all(r['document_id'] == doc['id'] for r in result['results'])))
        check('Empty search has no synthetic hits', lambda: expect(ok(client.get(f'/api/search/project/{pa}', params={'q': ''}))['results'] == []))
        check('Whitespace project name rejected', lambda: expect(client.post('/api/projects', json={'name': '  \n'}).status_code in (400, 422)))
        check('Whitespace document title rejected', lambda: expect(client.post('/api/documents', json={**payload, 'title': '  '}).status_code in (400, 422)))
        check('Whitespace document content rejected', lambda: expect(client.post('/api/documents', json={**payload, 'content': '\n  '}).status_code in (400, 422)))
        check('Nonexistent project rejected', lambda: expect(client.post('/api/documents', json={**payload, 'project_id': 2147483000}).status_code in (400, 404, 422)))
        check('Javascript source rejected', lambda: expect(client.post('/api/documents', json={**payload, 'source_url': 'javascript:alert(1)'}).status_code in (400, 422)))
        upload_data = {'project_id': str(pa), 'title': '上传验证'}
        check('UTF-8 Markdown upload', lambda: ok(client.post('/api/documents/upload', data=upload_data, files={'file': ('note.md', '上传后即可检索的中文资料。'.encode(), 'text/markdown')})))
        check('Unsupported extension rejected', lambda: expect(client.post('/api/documents/upload', data=upload_data, files={'file': ('bad.bin', b'hello', 'application/octet-stream')}).status_code in (400, 415, 422)))
        check('Invalid UTF-8 rejected', lambda: expect(client.post('/api/documents/upload', data=upload_data, files={'file': ('bad.txt', b'\xff\xfe\xfa', 'text/plain')}).status_code in (400, 422)))
        check('Upload URL validated', lambda: expect(client.post('/api/documents/upload', data={**upload_data, 'source_url': 'javascript:alert(1)'}, files={'file': ('bad.md', b'hello', 'text/plain')}).status_code in (400, 422)))
        check('Oversized upload rejected', lambda: expect(client.post('/api/documents/upload', data=upload_data, files={'file': ('large.txt', b'x'*(10*1024*1024+1), 'text/plain')}).status_code in (400, 413, 422)))
        card_body = {'project_id': pa, 'document_id': doc['id'], 'chunk_id': chunks[1]['id'], 'annotation': '验收批注'}
        card = ok(client.post('/api/cards', json=card_body))
        check('Cross-project card references rejected', lambda: expect(client.post('/api/cards', json={**card_body, 'project_id': pb}).status_code in (400, 404, 409, 422)))
        check('Mismatched document and chunk rejected', lambda: expect(client.post('/api/cards', json={**card_body, 'document_id': twin['id']}).status_code in (400, 404, 409, 422)))
        card_url = f'/api/cards/{card["id"]}?project_id={pa}'
        check('JSON annotation update persists', lambda: expect(ok(client.put(card_url, json={'project_id': pa, 'annotation': '更新后的真实批注'}))['annotation'] == '更新后的真实批注'))
        check('Unchanged annotation update succeeds', lambda: ok(client.put(card_url, json={'project_id': pa, 'annotation': '更新后的真实批注'})))
        check('Oversized annotation rejected', lambda: expect(client.put(card_url, json={'project_id': pa, 'annotation': 'a'*5001}).status_code in (400, 422)))
        md = ok(client.get(f'/api/cards/project/{pa}/export', params={'project_name': '主机验证'}))['markdown']
        check('Markdown contains original source and annotation', lambda: expect(all(s in md for s in ('https://example.com/notes', '合成验证资料', '更新后的真实批注', '间隔重复'))))
        check('Reading progress saved', lambda: expect(ok(client.put(f'/api/progress/document/{doc["id"]}', json={'status': 'read'}))['status'] == 'read'))
        check('Reading statistics query executes', lambda: expect(ok(client.get(f'/api/progress/project/{pa}/stats'))['read'] >= 1))
        concurrent_payload = {**payload, 'title': '并发导入', 'content': f'并发去重专属笔记 {tag}：必须只有一份完整原文。'}
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: client.post('/api/documents', json=concurrent_payload), range(4)))
        check('Concurrent duplicate imports remain atomic', lambda: expect(all(r.is_success for r in responses) and len({r.json()['id'] for r in responses}) == 1, '; '.join(str(r.status_code) for r in responses)))
        rollback_content = f'事务故障注入测试 {tag}'
        with patch.object(document_service, 'chunk_content', side_effect=RuntimeError('injected chunking failure')):
            client.post('/api/documents', json={**payload, 'content': rollback_content})
        with get_cursor() as cur:
            cur.execute('SELECT COUNT(*) AS n FROM documents WHERE project_id=%s AND content_hash=%s', (pa, hashlib.sha256(rollback_content.encode()).hexdigest()))
            remaining = cur.fetchone()['n']
        check('Failed import leaves no partial document', lambda: expect(remaining == 0))
    except Exception as exc:
        records.append({'check': 'Acceptance runner completed', 'passed': False, 'error': str(exc)[:600]})
        print('RUNNER FAILED:', str(exc)[:600])
    finally:
        for project_id in projects:
            response = client.delete(f'/api/projects/{project_id}')
            if not response.is_success:
                records.append({'check': 'Cleanup own project', 'passed': False, 'error': str(response.status_code)})

report = {'suite': 'Independent HTTP acceptance against real seekdb', 'checks': records,
          'passed': sum(r['passed'] for r in records), 'failed': sum(not r['passed'] for r in records)}
if len(sys.argv) > 1:
    Path(sys.argv[1]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'passed': report['passed'], 'failed': report['failed']}, ensure_ascii=False))
raise SystemExit(1 if report['failed'] else 0)
