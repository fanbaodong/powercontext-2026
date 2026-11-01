"""SQL adapter tests; mocks do not stand in for the real database demo."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from sdd_context.documents import DocumentStore, digest, source_info


class MemorySQL:
    """A small stateful cursor double for adapter boundaries, not a SQL server."""

    def __init__(self):
        self.rows = {}
        self.calls = []
        self.result = []
        self.cursor = MagicMock()
        self.cursor.__enter__.return_value = self.cursor
        self.cursor.execute.side_effect = self.execute
        self.cursor.fetchone.side_effect = lambda: self.result[0] if self.result else None
        self.cursor.fetchall.side_effect = lambda: list(self.result)
        self.connection = MagicMock()
        self.connection.__enter__.return_value = self.connection
        self.connection.cursor.return_value = self.cursor

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        normalized = " ".join(sql.split()).lower()
        self.result = []
        if normalized.startswith("select revision, sha256"):
            record = self.rows.get(tuple(params))
            self.result = [] if record is None else [record.copy()]
        elif normalized.startswith("insert into sdd_documents"):
            project, path, kind, revision, sha256, content = params
            self.rows[project, path] = dict(path=path, kind=kind, revision=revision,
                                           sha256=sha256, content=content)
        elif normalized.startswith("delete from sdd_documents"):
            project, *keep = params
            for key in list(self.rows):
                if key[0] == project and key[1] not in keep:
                    del self.rows[key]
        elif normalized.startswith("select path,kind,revision,sha256,content"):
            project = params[-1]
            terms = [value[1:-1] for value in params[:-1]]
            for (owner, _), record in self.rows.items():
                score = sum(term in record["content"].lower() for term in terms)
                if owner == project and score:
                    self.result.append({**record, "score": score})
            self.result.sort(key=lambda row: (-row["score"], row["path"]))
            self.result = self.result[:24]
        else:
            raise AssertionError(f"Unexpected adapter SQL: {sql}")


class SourceInfoTests(unittest.TestCase):
    def test_utf8_hash_and_ast_symbol_ranges_are_derived_from_real_content(self):
        content = ('# 标签\nclass Queue:\n'
                   '    def select(self):\n        return 1\n\n'
                   'async def refresh():\n    return 2\n')
        info = source_info("tickets.py", content)
        self.assertEqual(info["sha256"], hashlib.sha256(content.encode("utf-8")).hexdigest())
        self.assertEqual((info["start_line"], info["end_line"]), (1, 7))
        self.assertEqual(info["kind"], "code")
        ranges = {item["name"]: (item["start_line"], item["end_line"])
                  for item in info["symbols"]}
        self.assertEqual(ranges, {"Queue": (2, 4), "select": (3, 4), "refresh": (6, 7)})

    def test_source_type_and_hash_change_follow_content(self):
        self.assertEqual(source_info("specs/rules.md", "# Rules\n")["kind"], "spec")
        self.assertEqual(source_info("tests/test_tags.py", "pass\n")["kind"], "test")
        self.assertEqual(source_info("specs/rules.md", "# Rules\n")["symbols"], [])
        self.assertNotEqual(digest("Revision 1\n"), digest("Revision 2\n"))

    def test_invalid_python_is_reported_instead_of_inventing_symbols(self):
        with self.assertRaises(SyntaxError):
            source_info("bad.py", "def broken(:\n")


class DocumentStoreTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict("os.environ", {"SEEKDB_DATABASE": "minimax_unit"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.store = DocumentStore()
        self.sql = MemorySQL()
        connect_patch = patch.object(self.store, "connect", return_value=self.sql.connection)
        self.connect = connect_patch.start()
        self.addCleanup(connect_patch.stop)
        temporary = tempfile.TemporaryDirectory(prefix="minimax-documents-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_import_uses_parameters_and_revisions_follow_same_source_updates(self):
        project = "project' OR 1=1 --"
        spec_path = "specs/ticket's_rules.md"
        first_text = "# Ticket tags\nRevision 1: ANY requested tag.\n"
        second_text = "# Ticket tags\nRevision 2: ALL requested tags.\n"
        self.write(spec_path, first_text)
        self.write("tickets.py", "def select_tickets():\n    return []\n")
        self.write("fixtures.json", '{"excluded": true}')
        first = self.store.import_project(project, self.root)
        self.assertEqual({item["path"] for item in first["documents"]},
                         {spec_path, "tickets.py"})
        self.assertEqual({item["revision"] for item in first["documents"]}, {1})
        self.store.import_project(project, self.root)
        self.assertEqual(self.sql.rows[project, spec_path]["revision"], 1)
        self.write(spec_path, second_text)
        third = self.store.import_project(project, self.root)
        rows = {item["path"]: item for item in third["documents"]}
        self.assertEqual(rows[spec_path]["revision"], 2)
        self.assertEqual(rows["tickets.py"]["revision"], 1)
        self.assertEqual(self.sql.rows[project, spec_path]["content"], second_text)
        self.assertEqual(rows[spec_path]["sha256"], digest(second_text))
        for sql, parameters in self.sql.calls:
            self.assertNotIn(project, sql)
            self.assertNotIn(spec_path, sql)
            self.assertNotIn(second_text, sql)
            self.assertIsInstance(parameters, tuple)
        self.assertEqual(self.sql.connection.commit.call_count, 3)

    def test_different_queries_retrieve_different_database_content_and_parameters(self):
        self.write("specs/ticket_rules.md", "# Ticket tags\nTicket filter requires ALL tags.\n")
        self.write("specs/payments.md", "# Payment receipts\nPayment cents and currency.\n")
        self.store.import_project("demo", self.root)
        tickets = self.store.retrieve("demo", "ticket TICKET filter")
        payments = self.store.retrieve("demo", "payment currency")
        self.assertEqual(tickets["parameters"], ["%ticket%", "%filter%", "demo"])
        self.assertEqual(payments["parameters"], ["%payment%", "%currency%", "demo"])
        self.assertEqual([item["path"] for item in tickets["documents"]], ["specs/ticket_rules.md"])
        self.assertEqual([item["path"] for item in payments["documents"]], ["specs/payments.md"])
        self.assertIn("Payment cents", payments["documents"][0]["content"])
        self.assertNotIn("Ticket filter", payments["documents"][0]["content"])
        for response in (tickets, payments):
            self.assertIn("LIKE %s", response["sql"])
            self.assertIn("project_id=%s", response["sql"])
            self.assertNotIn(response["query"], response["sql"])
            for document in response["documents"]:
                self.assertEqual(document["sha256"], digest(document["content"]))

    def test_deleted_document_is_removed_and_cannot_be_retrieved(self):
        self.write("specs/tickets.md", "Ticket filtering rules\n")
        self.write("specs/payments.md", "Payment receipts\n")
        self.store.import_project("demo", self.root)
        self.store.import_project("other-project", self.root)
        (self.root / "specs/payments.md").unlink()
        self.store.import_project("demo", self.root)
        self.assertNotIn(("demo", "specs/payments.md"), self.sql.rows)
        self.assertIn(("other-project", "specs/payments.md"), self.sql.rows)
        self.assertEqual(self.store.retrieve("demo", "payment")["documents"], [])
        deletes = [(sql, params) for sql, params in self.sql.calls if sql.startswith("DELETE")]
        self.assertEqual(deletes[-1][1], ("demo", "specs/tickets.md"))
        self.assertNotIn("specs/tickets.md", deletes[-1][0])

    def test_corrupt_stored_content_is_rejected_by_hash(self):
        self.write("specs/tickets.md", "Ticket requirements\n")
        self.store.import_project("demo", self.root)
        self.sql.rows["demo", "specs/tickets.md"]["content"] = "Ticket tampered content\n"
        with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
            self.store.retrieve("demo", "ticket")

    def test_query_without_identifiers_is_rejected_before_connection(self):
        with self.assertRaisesRegex(ValueError, "English"):
            self.store.retrieve("demo", "标签 12")
        self.connect.assert_not_called()

    def test_nonmatching_query_returns_empty_instead_of_fixed_source_list(self):
        self.write("specs/tickets.md", "Ticket requirements\n")
        self.store.import_project("demo", self.root)
        self.assertEqual(self.store.retrieve("demo", "unrelatedword")["documents"], [])

    def test_database_identifier_rejects_sql_or_shared_database_names(self):
        for invalid in ("mysql", "minimax_x`; DROP TABLE x; --", "minimax_", "minimax_" + "a" * 49):
            with self.subTest(database=invalid), patch.dict("os.environ", {"SEEKDB_DATABASE": invalid}):
                with self.assertRaises(ValueError):
                    DocumentStore()


if __name__ == "__main__":
    unittest.main()
