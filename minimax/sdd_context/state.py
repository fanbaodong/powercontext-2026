"""Durable task state backed by the real PowerContext 1.2.0 Memory API.

Dependency: ``powercontext[builtin]==1.2.0``. API reference:
https://github.com/oceanbase/powercontext/blob/powercontext-v1.2.0/docs/zh/development/memory-layer.md

PowerContext owns the SQLite schema, transactions, and immutable revisions. This
adapter does not open SQLite or emulate a PowerContext service. Each operation
opens/closes a genuine runtime, so a later process can recover the same state.
"""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import version
from pathlib import Path
from typing import Any

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    RememberMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft


STATE_KIND = "minimax-sdd-task-state"
API_SOURCE = (
    "https://github.com/oceanbase/powercontext/blob/"
    "powercontext-v1.2.0/docs/zh/development/memory-layer.md"
)


def _validate_task_id(task_id: str) -> None:
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be a non-empty string")


def _normalise_state(task_id: str, status: dict[str, Any]) -> dict[str, Any]:
    """Validate the workflow contract and copy only JSON-serializable data."""
    _validate_task_id(task_id)
    if not isinstance(status, dict):
        raise TypeError("status must be a dictionary")
    if "task_id" in status and status["task_id"] != task_id:
        raise ValueError("status.task_id conflicts with the requested task_id")
    required = {"completed", "pending", "test_results", "code_version"}
    missing = sorted(required - status.keys())
    if missing:
        raise ValueError("Missing task-state fields: " + ", ".join(missing))
    for key in ("completed", "pending"):
        if not isinstance(status[key], list) or not all(isinstance(item, str) for item in status[key]):
            raise ValueError(f"{key} must be a list of strings")
    if not isinstance(status["test_results"], (dict, list)):
        raise ValueError("test_results must be a dictionary or list")
    if not isinstance(status["code_version"], str) or not status["code_version"].strip():
        raise ValueError("code_version must be a non-empty string")
    # Reject NaN/Infinity and non-JSON values rather than losing evidence during
    # recovery. Round-tripping also avoids retaining mutable caller references.
    return json.loads(json.dumps({**status, "task_id": task_id}, ensure_ascii=False, allow_nan=False))


class StateStore:
    """Append task checkpoints and recover the newest matching Memory revision.

    ``load`` returns the saved state itself. ``save`` returns that state plus a
    small evidence record containing public SDK/version and artifact identities.
    Neither return value includes runtime configuration or database credentials.
    """

    def __init__(self, path: Path, scope: str = "minimax-sdd") -> None:
        if not isinstance(scope, str) or not scope.strip():
            raise ValueError("scope must be a non-empty string")
        self.path = Path(path).expanduser().resolve()
        self.scope = scope

    def _runtime(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{self.path.as_posix()}")
        )
        # Keep all runtime-owned files beside the chosen persistent state DB,
        # rather than creating scheduler data in the caller's current directory.
        return open_builtin_runtime(
            config,
            scheduler_path=self.path.with_name(self.path.name + ".scheduler.db"),
        )

    async def _registered_scope_id(self, runtime) -> str:
        """Resolve our logical scope name through the SDK's durable registry.

        PowerContext 1.2.0 generates the actual scope ID. Passing a friendly
        label directly to memory.for_scope() does not create that scope.
        A stable idempotency key makes create() resolve the same registration
        after process restart, without a separate sidecar file or SQL access.
        """
        digest = hashlib.sha256(self.scope.encode("utf-8")).hexdigest()
        registered = await runtime.scopes.create(
            ScopeDraft(
                title=self.scope,
                summary="Durable checkpoints for the MiniMax SDD demonstration.",
                idempotency_key=f"minimax-sdd-task-state-v1:{digest}",
            )
        )
        return registered.scope_id

    async def load(self, task_id: str) -> dict[str, Any] | None:
        """Read by exact task identity, independent of FTS tokenization/ranking."""
        _validate_task_id(task_id)
        async with self._runtime() as runtime:
            scope_id = await self._registered_scope_id(runtime)
            page = await runtime.memory.for_scope(scope_id).list()
            matches: list[tuple[tuple[int, int, str], dict[str, Any]]] = []
            for record in page.entries:
                entry = record.entry
                if record.state != "active" or entry.kind != STATE_KIND:
                    continue
                try:
                    decoded = json.loads(entry.text)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(decoded, dict) or decoded.get("task_id") != task_id:
                    continue
                # list() returns references to the current head for every entry;
                # created_in_revision distinguishes successive append writes.
                order = (entry.created_in_revision, entry.version, entry.entry_version_id)
                matches.append((order, decoded))
            if not matches:
                return None
            _, latest = max(matches, key=lambda item: item[0])
            return _normalise_state(task_id, latest)

    async def save(self, task_id: str, status: dict[str, Any]) -> dict[str, Any]:
        """Commit an explicit Memory entry through the PowerContext SDK."""
        state = _normalise_state(task_id, status)
        text = json.dumps(state, ensure_ascii=False, sort_keys=True, allow_nan=False)
        async with self._runtime() as runtime:
            scope_id = await self._registered_scope_id(runtime)
            result = await runtime.memory.for_scope(scope_id).remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind=STATE_KIND, text=text),))
            )
            reference = result.memory_ref
            evidence: dict[str, Any] = {
                "backend": "powercontext",
                "package_version": version("powercontext"),
                "api_source": API_SOURCE,
                "operation": "runtime.memory.for_scope(scope).remember",
                "scope": self.scope,
                "scope_id": scope_id,
                "task_id": task_id,
                "previous_revision": result.previous_revision,
                "memory_ref": {
                    "family": reference.family,
                    "artifact_id": reference.artifact_id,
                    "revision": reference.revision,
                },
            }
            if result.entry is not None:
                evidence["entry_id"] = result.entry.entry.entry_id
                evidence["entry_version_id"] = result.entry.entry.entry_version_id
            return {"state": state, "evidence": evidence}
