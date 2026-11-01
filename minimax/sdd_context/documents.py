"""Import versioned project documents and retrieve context using real SQL."""
from __future__ import annotations

import ast
import hashlib
import os
import re
from pathlib import Path

import pymysql


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def source_info(path: str, content: str) -> dict:
    kind = 'spec' if path.endswith('.md') else ('test' if path.startswith('tests/') else 'code')
    symbols = []
    if path.endswith('.py'):
        for node in ast.walk(ast.parse(content)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols.append({'name': node.name, 'start_line': node.lineno, 'end_line': node.end_lineno})
    return {'path': path, 'kind': kind, 'sha256': digest(content), 'start_line': 1,
            'end_line': len(content.splitlines()), 'symbols': symbols}


class DocumentStore:
    def __init__(self):
        database = os.getenv('SEEKDB_DATABASE', 'minimax_sdd')
        if not re.fullmatch(r'minimax_[a-zA-Z0-9_]{1,48}', database):
            raise ValueError('Use a dedicated database named minimax_<name>')
        self.database = database
        self.options = dict(host=os.getenv('SEEKDB_HOST', '127.0.0.1'),
                            port=int(os.getenv('SEEKDB_PORT', '2893')),
                            user=os.getenv('SEEKDB_USER', 'root'),
                            password=os.getenv('SEEKDB_PASSWORD', ''),
                            charset='utf8mb4', connect_timeout=10, read_timeout=30,
                            write_timeout=30, cursorclass=pymysql.cursors.DictCursor)

    def connect(self, *, server=False):
        return pymysql.connect(**self.options, **({} if server else {'database': self.database}))

    def initialize(self):
        with self.connect(server=True) as connection:
            with connection.cursor() as cur:
                cur.execute('SELECT VERSION() AS version, @@version_comment AS version_comment')
                identity = cur.fetchone()
                if not any(s in str(identity).lower() for s in ('oceanbase', 'seekdb')):
                    raise RuntimeError('This task requires an actual seekdb/OceanBase server')
                cur.execute(f'CREATE DATABASE IF NOT EXISTS `{self.database}`')
                cur.execute(f'USE `{self.database}`')
                cur.execute('''CREATE TABLE IF NOT EXISTS sdd_documents (
                    project_id VARCHAR(80) NOT NULL, path VARCHAR(384) NOT NULL,
                    kind VARCHAR(16) NOT NULL, revision INT NOT NULL,
                    sha256 CHAR(64) NOT NULL, content LONGTEXT NOT NULL,
                    PRIMARY KEY(project_id, path))''')
            connection.commit()
        return identity

    def import_project(self, project_id: str, root: Path) -> dict:
        files = sorted(p for p in root.rglob('*') if p.is_file() and p.suffix in ('.py', '.md'))
        if not files:
            raise ValueError('No project sources found')
        imported = []
        with self.connect() as connection:
            with connection.cursor() as cur:
                for path in files:
                    relative = path.relative_to(root).as_posix()
                    content = path.read_text(encoding='utf-8')
                    info = source_info(relative, content)
                    cur.execute('SELECT revision, sha256 FROM sdd_documents WHERE project_id=%s AND path=%s',
                                (project_id, relative))
                    old = cur.fetchone()
                    revision = (old['revision'] + int(old['sha256'] != info['sha256'])) if old else 1
                    cur.execute('''INSERT INTO sdd_documents(project_id,path,kind,revision,sha256,content)
                        VALUES (%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
                        kind=VALUES(kind),revision=VALUES(revision),sha256=VALUES(sha256),content=VALUES(content)''',
                                (project_id, relative, info['kind'], revision, info['sha256'], content))
                    imported.append({**info, 'revision': revision})
                # Deleted sources must not remain eligible for retrieval.
                names = [p.relative_to(root).as_posix() for p in files]
                placeholders = ','.join(['%s'] * len(names))
                cur.execute(f'DELETE FROM sdd_documents WHERE project_id=%s AND path NOT IN ({placeholders})',
                            (project_id, *names))
            connection.commit()
        return {'operation': 'upsert current project sources', 'database': self.database,
                'project_id': project_id, 'documents': imported}

    def retrieve(self, project_id: str, query: str) -> dict:
        # Tokens come from the incoming task, not a fixed answer/source list.
        stopwords = {'the', 'and', 'under', 'implement', 'preserve', 'revision', 'all', 'any', 'behavior', 'with', 'for'}
        tokens = [word for word in dict.fromkeys(re.findall(r'[a-zA-Z][a-zA-Z0-9_]{2,}', query.lower()))
                  if word not in stopwords][:32]
        if not tokens:
            raise ValueError('Include English project identifiers in task query')
        score = '+'.join(['CASE WHEN LOWER(content) LIKE %s THEN 1 ELSE 0 END'] * len(tokens))
        sql = f'''SELECT path,kind,revision,sha256,content,({score}) AS score
            FROM sdd_documents WHERE project_id=%s HAVING score>0 ORDER BY score DESC,path LIMIT 24'''
        params = tuple('%' + token.replace('_', '\\_') + '%' for token in tokens) + (project_id,)
        with self.connect() as connection:
            with connection.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        selected = []
        for row in rows:
            info = source_info(row['path'], row['content'])
            if info['sha256'] != row['sha256']:
                raise RuntimeError('Stored source hash mismatch')
            selected.append({**info, 'revision': row['revision'], 'score': int(row['score']), 'content': row['content']})
        return {'database': self.database, 'query': query, 'sql': sql, 'parameters': list(params),
                'documents': selected}
