"""Adapter tests plus real SDK persistence without model or network calls."""

import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from sdd_context.state import STATE_KIND, StateStore


def checkpoint(task_id="task-1", code_version="before"):
    return {
        "task_id": task_id,
        "completed": ["资料已导入"],
        "pending": ["run target tests"],
        "test_results": {"target": {"exit_code": 1}},
        "code_version": code_version,
    }


def memory_record(state, revision, *, kind=STATE_KIND, active=True):
    return SimpleNamespace(
        state="active" if active else "inactive",
        entry=SimpleNamespace(
            kind=kind,
            text=json.dumps(state, ensure_ascii=False),
            created_in_revision=revision,
            version=1,
            entry_id=f"entry-{revision}",
            entry_version_id=f"version-{revision}",
        ),
    )


class StateStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = StateStore(Path("unused-test-state.db"))
        self.scoped = SimpleNamespace(list=AsyncMock(), remember=AsyncMock())
        self.registered_scope_id = "sc_registered_minimax_test"
        self.runtime = SimpleNamespace(
            memory=SimpleNamespace(for_scope=Mock(return_value=self.scoped)),
            scopes=SimpleNamespace(create=AsyncMock(return_value=SimpleNamespace(scope_id=self.registered_scope_id))),
        )

        @asynccontextmanager
        async def mock_runtime():
            yield self.runtime

        self.runtime_patch = patch.object(self.store, "_runtime", side_effect=mock_runtime)
        self.runtime_patch.start()
        self.addCleanup(self.runtime_patch.stop)

    async def test_load_empty_scope_returns_none(self):
        self.scoped.list.return_value = SimpleNamespace(entries=())
        self.assertIsNone(await self.store.load("task-1"))
        self.runtime.scopes.create.assert_awaited_once()
        draft = self.runtime.scopes.create.call_args.args[0]
        self.assertEqual(draft.title, "minimax-sdd")
        self.assertTrue(draft.idempotency_key.startswith("minimax-sdd-task-state-v1:"))
        self.runtime.memory.for_scope.assert_called_once_with(self.registered_scope_id)

    async def test_scope_registration_is_idempotent_across_operations(self):
        self.scoped.list.return_value = SimpleNamespace(entries=())
        await self.store.load("task-1")
        await self.store.load("task-1")
        drafts = [call.args[0] for call in self.runtime.scopes.create.call_args_list]
        self.assertEqual(drafts[0], drafts[1])
        self.assertEqual(self.runtime.memory.for_scope.call_count, 2)

    async def test_load_matches_exact_task_and_newest_revision_not_list_order(self):
        expected = checkpoint(code_version="after")
        records = [
            memory_record(checkpoint("task-10", "unrelated"), 99),
            memory_record(expected, 7),
            memory_record(checkpoint(code_version="old"), 2),
            memory_record(checkpoint(code_version="wrong-kind"), 100, kind="decision"),
            memory_record(checkpoint(code_version="inactive"), 101, active=False),
        ]
        malformed = memory_record({}, 200)
        malformed.entry.text = "not JSON"
        self.scoped.list.return_value = SimpleNamespace(entries=(*records, malformed))
        self.assertEqual(await self.store.load("task-1"), expected)
        self.scoped.remember.assert_not_called()

    async def test_save_uses_sdk_request_and_returns_only_safe_evidence(self):
        state = checkpoint()
        entry = memory_record(state, 4)
        self.scoped.remember.return_value = SimpleNamespace(
            previous_revision=3,
            memory_ref=SimpleNamespace(family="memory", artifact_id="memory-safe-id", revision=4),
            entry=entry,
        )
        with patch("sdd_context.state.version", return_value="1.2.0"):
            result = await self.store.save("task-1", state)
        request = self.scoped.remember.call_args.args[0]
        self.assertEqual(request.entries[0].kind, STATE_KIND)
        self.assertEqual(json.loads(request.entries[0].text), state)
        self.assertEqual(result["state"], state)
        self.assertEqual(result["evidence"]["package_version"], "1.2.0")
        self.assertEqual(result["evidence"]["memory_ref"]["revision"], 4)
        self.assertEqual(result["evidence"]["entry_id"], "entry-4")
        self.assertEqual(result["evidence"]["scope"], "minimax-sdd")
        self.assertEqual(result["evidence"]["scope_id"], self.registered_scope_id)
        self.assertNotIn("url", result["evidence"])
        state["completed"].append("caller mutated later")
        self.assertNotEqual(result["state"], state)

    async def test_save_attaches_task_id_if_caller_omits_it(self):
        state = checkpoint()
        del state["task_id"]
        self.scoped.remember.return_value = SimpleNamespace(
            previous_revision=None,
            memory_ref=SimpleNamespace(family="memory", artifact_id="memory-id", revision=1),
            entry=None,
        )
        with patch("sdd_context.state.version", return_value="1.2.0"):
            result = await self.store.save("task-1", state)
        self.assertEqual(result["state"]["task_id"], "task-1")
        self.assertNotIn("task_id", state)

    async def test_save_rejects_conflicting_identity_before_opening_runtime(self):
        with self.assertRaisesRegex(ValueError, "conflicts"):
            await self.store.save("task-1", checkpoint("task-2"))
        self.store._runtime.assert_not_called()

    async def test_save_rejects_invalid_state_without_writing(self):
        bad_states = [{}, {**checkpoint(), "pending": "not a list"},
                      {**checkpoint(), "test_results": {"duration": float("nan")}}]
        for state in bad_states:
            with self.subTest(state=state), self.assertRaises(ValueError):
                await self.store.save("task-1", state)
        self.scoped.remember.assert_not_called()

    async def test_load_does_not_silently_fall_back_when_latest_state_is_invalid(self):
        invalid = {**checkpoint(), "completed": "corrupt checkpoint"}
        self.scoped.list.return_value = SimpleNamespace(entries=(
            memory_record(checkpoint(), 1), memory_record(invalid, 2),
        ))
        with self.assertRaisesRegex(ValueError, "completed"):
            await self.store.load("task-1")

    async def test_blank_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            await self.store.load(" ")
        self.store._runtime.assert_not_called()


class StateStoreRealSdkTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_scope_save_and_reopen_use_actual_registered_scope(self):
        # No generation/embedding provider is configured. These calls exercise
        # only PowerContext's local scope registry and SQLite Memory backend.
        with tempfile.TemporaryDirectory(prefix="minimax-state-sdk-") as directory:
            path = Path(directory) / "state.db"
            initial = StateStore(path)
            self.assertIsNone(await initial.load("task-1"))
            before = await initial.save("task-1", checkpoint())
            self.assertEqual(before["evidence"]["backend"], "powercontext")
            self.assertEqual(before["evidence"]["package_version"], "1.2.0")

            # Each adapter method also opens and closes a real runtime, and a
            # new adapter must find the same scope instead of making another.
            reopened = StateStore(path)
            self.assertEqual(await reopened.load("task-1"), checkpoint())
            after_state = checkpoint(code_version="after")
            after = await reopened.save("task-1", after_state)
            self.assertEqual(before["evidence"]["scope_id"], after["evidence"]["scope_id"])
            self.assertEqual(await StateStore(path).load("task-1"), after_state)
            self.assertIsNone(await StateStore(path, scope="another-workflow").load("task-1"))


if __name__ == "__main__":
    unittest.main()
