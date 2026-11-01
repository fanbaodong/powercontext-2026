"""Offline workflow boundary tests; no model, database, or generated code runs."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sdd_context import workflow


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(
            prefix="minimax-workflow-test-", dir=Path(__file__).resolve().parent
        )
        self.addCleanup(temporary.cleanup)
        self.run = Path(temporary.name)
        self.work = self.run / "work"
        self.work.mkdir()
        self.old_source = "def select_tickets(tickets):\n    return list(tickets)\n"
        self.new_source = "def select_tickets(tickets):\n    return sorted(tickets, key=lambda t: t['id'])\n"
        self.source = self.work / "tickets.py"
        self.source.write_text(self.old_source, encoding="utf-8")
        (self.work / "tests").mkdir()
        (self.work / "tests/test_tags.py").write_text("# immutable target tests\n", encoding="utf-8")
        (self.work / "tests/test_regression.py").write_text("# immutable regression tests\n", encoding="utf-8")
        shutil.copytree(self.work, self.run / "before")
        self.task = {
            "task_id": "unit-test-task",
            "project_id": "unit-test-project",
            "editable_file": "tickets.py",
            "query": "select_tickets filter",
            "required_sources": ["tickets.py"],
            "required_symbols": ["select_tickets"],
            "distractor_sources": [],
            "commands": {
                "target": ["python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_tags.py", "-v"],
                "regression": ["python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_regression.py", "-v"],
            },
            "expected_after_cli": {"argv": ["python", "tickets.py"], "ids": [9]},
        }
        workflow.write_json(self.run / "task.json", self.task)
        self.state = {
            "task_id": self.task["task_id"],
            "completed": ["import", "spec-v2", "baseline-tests"],
            "pending": ["minimax-change", "after-tests"],
            "test_results": {"target": {"exit_code": 1}, "regression": {"exit_code": 0}},
            "code_version": workflow.version(self.work)[0],
            "writer_pid": max(os.getpid() - 1, 1),
        }
        self.state_store = SimpleNamespace(load=AsyncMock(return_value=self.state), save=AsyncMock())
        self.context = {
            "documents": [{"path": "tickets.py", "sha256": workflow.digest(self.old_source),
                           "content": self.old_source}],
            "task_state": self.state,
        }
        self.state_class = self.patch("StateStore", return_value=self.state_store)
        self.document_class = self.patch("DocumentStore")
        self.context_builder = self.patch("context_for", return_value=self.context)
        self.model = self.patch("generate_change")
        self.tests = self.patch("run_tests")

    def patch(self, name, **kwargs):
        patcher = patch.object(workflow, name, **kwargs)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def evidence(self, *, path="tickets.py"):
        supplied = {"task": self.task, "retrieved_context": self.context,
                    "source_path": "tickets.py", "source_text": self.old_source}
        return {
            "mode": "live-model-request",
            "before_source_sha256": workflow.digest(self.old_source),
            "request": {"body": {"messages": [{"role": "system", "content": "test"},
                                                   {"role": "user", "content": json.dumps(supplied)}]}},
            "result": {"path": path, "content": self.new_source, "summary": "Synthetic unit-test candidate"},
        }

    def save_record(self, record):
        workflow.write_json(self.run / "minimax-record.json", record)

    def assert_no_application(self):
        self.assertEqual(self.source.read_text(encoding="utf-8"), self.old_source)
        self.tests.assert_not_called()
        self.state_store.save.assert_not_awaited()
        self.assertFalse((self.run / "after").exists())

    def test_generate_only_saves_candidate_without_applying_or_executing_tests(self):
        self.model.return_value = self.evidence()
        result = workflow.resume(self.run, generate=True)
        self.assertTrue(result["generated"])
        self.model.assert_called_once_with(self.task, self.context, self.old_source)
        self.assertEqual((self.run / "candidate.py").read_text(encoding="utf-8"), self.new_source)
        record = workflow.read_json(self.run / "minimax-record.json")
        self.assertEqual(record["before_source_sha256"], workflow.digest(self.old_source))
        self.assert_no_application()

    def test_apply_rejects_source_hash_from_a_different_baseline(self):
        record = self.evidence()
        record["before_source_sha256"] = workflow.digest("different original source")
        self.save_record(record)
        with self.assertRaisesRegex(RuntimeError, "different baseline"):
            workflow.resume(self.run, apply_candidate=True)
        self.model.assert_not_called()
        self.assert_no_application()

    def test_apply_binds_actual_request_source_and_context_to_current_run(self):
        for field in ("source_text", "task", "retrieved_context"):
            with self.subTest(field=field):
                record = self.evidence()
                supplied = json.loads(record["request"]["body"]["messages"][1]["content"])
                if field == "source_text":
                    supplied[field] = "unrelated source"
                elif field == "task":
                    supplied[field]["task_id"] = "another-task"
                else:
                    supplied[field]["documents"][0]["sha256"] = "0" * 64
                record["request"]["body"]["messages"][1]["content"] = json.dumps(supplied)
                self.save_record(record)
                with self.assertRaisesRegex(RuntimeError, "does not match"):
                    workflow.resume(self.run, apply_candidate=True)
                self.assert_no_application()

    def test_apply_enforces_exact_editable_filename_allowlist(self):
        for forbidden in ("../tickets.py", "sample/tickets.py", "tests/test_tags.py", "/tmp/tickets.py"):
            with self.subTest(path=forbidden):
                self.save_record(self.evidence(path=forbidden))
                with self.assertRaisesRegex(ValueError, "only modify tickets.py"):
                    workflow.resume(self.run, apply_candidate=True)
                self.assert_no_application()
        self.model.assert_not_called()

    def test_completed_state_is_reused_without_retrieval_model_or_tests(self):
        self.state["completed"].extend(["minimax-change", "after-tests"])
        self.state["pending"] = []
        result = workflow.resume(self.run, generate=True)
        self.assertTrue(result["state_reused"])
        self.assertIn("already-validated", result["action"])
        self.assertEqual(result["code_version"], self.state["code_version"])
        self.context_builder.assert_not_called()
        self.document_class.assert_not_called()
        self.model.assert_not_called()
        self.tests.assert_not_called()
        self.state_store.save.assert_not_awaited()
        restored = workflow.read_json(self.run / "state-restored-after.json")
        self.assertEqual(restored["state"], self.state)

    def test_failed_tests_restore_saved_source_and_do_not_claim_success(self):
        self.save_record(self.evidence())
        before_version = workflow.version(self.work)[0]
        self.tests.return_value = {
            "target": {"exit_code": 1, "suite_ok": False, "executed_tests": 7},
            "regression": {"exit_code": 0, "suite_ok": True, "executed_tests": 12},
        }
        with self.assertRaisesRegex(RuntimeError, "failed tests"):
            workflow.resume(self.run, apply_candidate=True)
        self.tests.assert_called_once_with(self.run, "after", self.task)
        self.assertEqual(workflow.version(self.work)[0], before_version)
        self.state_store.save.assert_not_awaited()
        self.assertFalse((self.run / "after").exists())
        self.assertFalse((self.run / "state-saved-after.json").exists())
        self.assertTrue((self.run / "minimax-record.json").is_file())
        # The same persisted baseline remains usable by a later resume.
        self.tests.reset_mock()
        resumed = workflow.resume(self.run)
        self.assertTrue(resumed["state_reused"])
        self.tests.assert_not_called()

    def test_test_runner_timeout_restores_saved_source(self):
        self.save_record(self.evidence())
        self.tests.side_effect = subprocess.TimeoutExpired(["python", "-m", "unittest"], 60)
        with self.assertRaises(subprocess.TimeoutExpired):
            workflow.resume(self.run, apply_candidate=True)
        self.assertEqual(self.source.read_text(encoding="utf-8"), self.old_source)
        self.state_store.save.assert_not_awaited()
        self.assertFalse((self.run / "after").exists())

    def test_stale_work_is_rejected_before_model_or_database_access(self):
        self.source.write_text("# local uncheckpointed edit\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "differs from saved task version"):
            workflow.resume(self.run, generate=True)
        self.model.assert_not_called()
        self.document_class.assert_not_called()
        self.tests.assert_not_called()


class TestExecutionEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(
            prefix="minimax-log-test-", dir=Path(__file__).resolve().parent
        )
        self.addCleanup(temporary.cleanup)
        self.run = Path(temporary.name)
        (self.run / "work").mkdir()
        (self.run / "work/tickets.py").write_text("# harmless version input\n", encoding="utf-8")
        self.task = {"commands": {
            "target": ["python", "-m", "unittest", "discover", "-p", "test_tags.py"],
            "regression": ["python", "-m", "unittest", "discover", "-p", "test_regression.py"],
        }}

    def test_zero_exit_without_actual_tests_is_rejected(self):
        skipped = SimpleNamespace(returncode=0, stdout="Ran 0 tests in 0.000s\n\nOK\n")
        with patch.object(workflow.subprocess, "run", return_value=skipped):
            with self.assertRaisesRegex(RuntimeError, "actually execute"):
                workflow.run_tests(self.run, "after", self.task)
        self.assertEqual(workflow.read_json(self.run / "after-tests.json")["results"]["target"]["executed_tests"], 0)

    def test_test_subprocess_environment_excludes_credentials(self):
        successful = [SimpleNamespace(returncode=0, stdout=f"Ran {count} tests in 0.001s\n\nOK\n")
                      for count in (7, 12)]
        environment = {"MINIMAX_API_KEY": "synthetic-test-key", "SEEKDB_PASSWORD": "synthetic-db-password",
                       "UNRELATED_SECRET": "synthetic-secret", "PATH": "safe-test-path"}
        with patch.dict(os.environ, environment, clear=True):
            with patch.object(workflow.subprocess, "run", side_effect=successful) as runner:
                results = workflow.run_tests(self.run, "after", self.task)
        self.assertEqual(results["target"]["executed_tests"], 7)
        self.assertEqual(results["regression"]["executed_tests"], 12)
        self.assertTrue(all(item["suite_ok"] for item in results.values()))
        for invocation in runner.call_args_list:
            child_env = invocation.kwargs["env"]
            self.assertEqual(child_env["PATH"], "safe-test-path")
            for secret in ("MINIMAX_API_KEY", "SEEKDB_PASSWORD", "UNRELATED_SECRET"):
                self.assertNotIn(secret, child_env)


if __name__ == "__main__":
    unittest.main()
