# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Source dependency lifecycle: registration, propagation, and native effects.

Each Scope owns one directed acyclic graph of evidence nodes. A Source event
withdraws or restores the Source and the same ``all`` / ``any`` policy that
derived a node's availability at registration propagates the new state to every
descendant. Withdrawn Memory entries become inactive and withdrawn Topic Memory
artifacts leave Topic search and Prepared Context while both keep their exact
immutable revision readable through the ``audit`` purpose.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, AsyncIterator, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.memory import MemoryService
from powercontext.builtin.persistence.lifecycle import (
    PENDING_EVENT,
    MEMORY_NODE,
    SOURCE_NODE,
    TOPIC_NODE,
    StoredLifecycleEvent,
    StoredLifecycleNode,
)
from powercontext.builtin.sources import validate_scope_id
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime

GENERATION_PURPOSE = "generation"
AUDIT_PURPOSE = "audit"
_PURPOSES = frozenset({GENERATION_PURPOSE, AUDIT_PURPOSE})
_POLICIES = frozenset({"all", "any"})
# Memory keeps the audit reason on each revision transition; the canonical
# Memory value rejects anything longer, so native writes clamp while the
# lifecycle event keeps the caller's text intact.
_MAX_NATIVE_REASON_LENGTH = 512


class SourceLifecycleError(ValueError):
    """Reject an invalid Source Lifecycle request or an unavailable reference."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message if message is not None else _MESSAGES.get(code, code))


_MESSAGES = {
    "node-id": "node_id must be a non-empty, non-blank string",
    "node-conflict": "node_id is already registered with a different binding",
    "node-missing": "node_id is not registered in this scope",
    "not-source": "lifecycle events apply only to registered Sources",
    "policy": "policy must be 'all' or 'any'",
    "parents": "a derived node requires at least one registered parent",
    "parent-duplicate": "parents must be unique",
    "parent-self": "a node cannot depend on itself",
    "parent-missing": "every parent must be registered before its child",
    "revision": "revision must be a positive integer",
    "sequence": "sequence must be a positive integer",
    "revision-mismatch": "event revision does not match the Source lifecycle revision",
    "sequence-order": "event sequence must increase monotonically",
    "sequence-conflict": "the same sequence was already accepted with different content",
    "event": "expected a SourceLifecycleEvent",
    "purpose": "purpose must be 'generation' or 'audit'",
    "unavailable": "the node is not available for generation",
    "unreadable-source": "the registered Source is not readable in this scope",
    "unreadable-memory": "the registered MemoryCitation is not readable in this scope",
    "unreadable-topic": "the registered Topic Memory ArtifactRef is not readable in this scope",
    "memory-missing": "the registered Memory artifact is not readable",
    "entry-missing": "the registered Memory entry is not present in the current revision",
    "store": "Source Lifecycle storage is not configured",
}


class SourceLifecycleEvent(BaseModel):
    """One accepted change to a Source's lifecycle state.

    The model is frozen and strict: unknown fields, blank identifiers, and
    non-positive versions or sequences are rejected before any work happens.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    node_id: Annotated[str, Field(min_length=1, max_length=MAX_SOURCE_ID_LENGTH)]
    revision: Annotated[int, Field(gt=0)]
    sequence: Annotated[int, Field(gt=0)]
    available: bool
    reason: Annotated[str, Field(min_length=1)]

    @field_validator("node_id", "reason")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must contain at least one non-whitespace character")  # noqa: TRY003
        return value


class SourceLifecycleStore(Protocol):
    """Durable state used by one scope's lifecycle service."""

    async def load(
        self,
        scope_id: str,
        /,
    ) -> tuple[tuple[StoredLifecycleNode, ...], tuple[StoredLifecycleEvent, ...]]: ...

    async def insert_node(self, scope_id: str, node: StoredLifecycleNode, /) -> None: ...

    async def insert_event(self, scope_id: str, event: StoredLifecycleEvent, /) -> None: ...

    async def complete_event(
        self,
        scope_id: str,
        event: StoredLifecycleEvent,
        availability: Mapping[str, bool],
        /,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class _Node:
    node_id: str
    kind: str
    order: int
    policy: str | None
    parents: tuple[str, ...]
    reference: SourceRef | MemoryCitation | ArtifactRef
    available: bool
    revision: int
    sequence: int


class SourceLifecycleApplication:
    """Select the Source Lifecycle service for one scope."""

    def __init__(self, runtime: BuiltinRuntime, store: SourceLifecycleStore | None) -> None:
        self._runtime = runtime
        self._store = store

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        if self._store is None:
            raise SourceLifecycleError("store")
        return ScopedSourceLifecycle(self._runtime, self._store, scope_id)


class ScopedSourceLifecycle:
    """Register evidence nodes and apply Source lifecycle events in one scope."""

    def __init__(
        self,
        runtime: BuiltinRuntime,
        store: SourceLifecycleStore,
        scope_id: str,
    ) -> None:
        self._runtime = runtime
        self._store = store
        self.scope_id = validate_scope_id(scope_id)

    # ------------------------------------------------------------------ register

    async def register_source(
        self,
        node_id: str,
        source_ref: SourceRef,
        revision: int,
    ) -> None:
        """Register one captured Source at an explicit lifecycle revision."""

        node = _require_node_id(node_id)
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("unreadable-source", "source_ref must be a SourceRef")
        lifecycle_revision = _require_positive_int(revision, "revision")
        await self._register(
            node,
            SOURCE_NODE,
            source_ref,
            parents=(),
            policy=None,
            revision=lifecycle_revision,
        )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        parents: Sequence[str] = (),
        policy: str = "all",
    ) -> None:
        """Register one exact Memory entry derived from already registered nodes."""

        node = _require_node_id(node_id)
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("unreadable-memory", "citation must be a MemoryCitation")
        parent_ids = _require_parents(node, parents)
        selected_policy = _require_policy(policy)
        await self._register(
            node,
            MEMORY_NODE,
            citation,
            parents=parent_ids,
            policy=selected_policy,
            revision=0,
        )

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        parents: Sequence[str] = (),
        policy: str = "all",
    ) -> None:
        """Register one Topic Memory revision derived from registered nodes."""

        node = _require_node_id(node_id)
        if not isinstance(artifact_ref, ArtifactRef):
            raise SourceLifecycleError("unreadable-topic", "artifact_ref must be an ArtifactRef")
        parent_ids = _require_parents(node, parents)
        selected_policy = _require_policy(policy)
        await self._register(
            node,
            TOPIC_NODE,
            artifact_ref,
            parents=parent_ids,
            policy=selected_policy,
            revision=0,
        )

    async def _register(
        self,
        node_id: str,
        kind: str,
        reference: SourceRef | MemoryCitation | ArtifactRef,
        *,
        parents: tuple[str, ...],
        policy: str | None,
        revision: int,
    ) -> None:
        payload = reference.model_dump_json()
        async with self._runtime._scoped_operation(self.scope_id), self._runtime._locked(self.scope_id):
            nodes, _ = await self._load()
            by_id = {node.node_id: node for node in nodes}
            current = by_id.get(node_id)
            if current is not None:
                if (
                    current.kind == kind
                    and current.parents == parents
                    and current.policy == policy
                    and current.revision == revision
                    and current.reference == reference
                ):
                    # A retried registration whose native write failed must finish
                    # the effect it already committed the registration for.
                    await self._apply_native(current, current.available, reason=None)
                    return
                raise SourceLifecycleError("node-conflict", f"node {node_id!r} is already registered")

            for parent in parents:
                registered = by_id.get(parent)
                if registered is None:
                    raise SourceLifecycleError("parent-missing", f"parent {parent!r} is not registered")
            # A derived node registered after its evidence was withdrawn starts
            # unavailable; a Source starts available until an event withdraws it.
            available = (
                True
                if kind == SOURCE_NODE
                else _support(parents, _require_policy(policy), {node.node_id: node.available for node in nodes})
            )
            await self._require_readable(kind, reference)
            stored = StoredLifecycleNode(
                node_id=node_id,
                kind=kind,
                registration_order=len(nodes) + 1,
                payload=payload,
                policy=policy,
                parents=parents,
                available=available,
                lifecycle_revision=revision,
                last_sequence=0,
            )
            await self._store.insert_node(self.scope_id, stored)
            await self._apply_native(self._node(stored), available, reason=None)

    # ----------------------------------------------------------------------- apply

    async def apply(self, event: SourceLifecycleEvent | Mapping[str, Any], /) -> list[str]:
        """Apply one Source event and return every currently available node id."""

        selected = _require_event(event)
        async with self._runtime._scoped_operation(self.scope_id), self._runtime._locked(self.scope_id):
            nodes, events = await self._load()
            pending = sorted(
                (item for item in events if item.status == PENDING_EVENT),
                key=lambda item: (item.node_id, item.sequence),
            )
            retried = next((item for item in pending if _same_event(item, selected)), None)
            if retried is not None:
                # The same event retried after its native write was interrupted
                # finishes the propagation it started.
                source = next((node for node in nodes if node.node_id == retried.node_id), None)
                if source is None:
                    raise SourceLifecycleError("not-source", retried.node_id)
                state = await self._propagate(source, _event_model(retried), nodes)
                return _active_ids(state)
            blocking = [
                item
                for item in pending
                if item.node_id == selected.node_id and item.sequence >= selected.sequence
            ]
            if blocking:
                raise SourceLifecycleError(
                    "sequence-conflict" if blocking[0].sequence == selected.sequence else "sequence-order"
                )
            if pending:
                nodes, events = await self._recover_pending(pending)
            by_id = {node.node_id: node for node in nodes}
            source = by_id.get(selected.node_id)
            if source is None or source.kind != SOURCE_NODE:
                raise SourceLifecycleError("not-source", f"source {selected.node_id!r} is not registered")
            if selected.revision != source.revision:
                raise SourceLifecycleError("revision-mismatch", f"source {selected.node_id!r} has another revision")
            history = sorted(
                (item for item in events if item.node_id == selected.node_id),
                key=lambda item: item.sequence,
            )
            same = next((item for item in history if item.sequence == selected.sequence), None)
            if same is not None:
                if (same.revision, same.available, same.reason) != (
                    selected.revision,
                    selected.available,
                    selected.reason,
                ):
                    raise SourceLifecycleError("sequence-conflict")
                if same.status == PENDING_EVENT:
                    state = await self._propagate(source, selected, nodes)
                    return _active_ids(state)
                return _active_ids({node.node_id: node.available for node in nodes})
            latest = history[-1].sequence if history else 0
            if selected.sequence <= latest:
                raise SourceLifecycleError("sequence-order")
            stored = StoredLifecycleEvent(
                node_id=selected.node_id,
                sequence=selected.sequence,
                revision=selected.revision,
                available=selected.available,
                reason=selected.reason,
                status=PENDING_EVENT,
            )
            await self._store.insert_event(self.scope_id, stored)
            state = await self._propagate(source, selected, nodes)
            return _active_ids(state)

    async def _propagate(
        self,
        source: _Node,
        event: SourceLifecycleEvent,
        nodes: tuple[_Node, ...],
    ) -> dict[str, bool]:
        state = {node.node_id: node.available for node in nodes}
        state[source.node_id] = event.available
        for node in nodes:
            if node.kind == SOURCE_NODE:
                continue
            state[node.node_id] = _support(node.parents, node.policy or "all", state)
        changed = {
            node.node_id: state[node.node_id] for node in nodes if state[node.node_id] != node.available
        }
        # One Memory transaction per artifact keeps a propagation interruption
        # from leaving a half-updated entry set behind.
        memory_changes: dict[str, dict[str, bool]] = {}
        for node in nodes:
            if node.node_id not in changed or node.kind != MEMORY_NODE:
                continue
            citation = node.reference
            if not isinstance(citation, MemoryCitation):  # pragma: no cover - kind invariant
                continue
            memory_changes.setdefault(citation.memory_ref.artifact_id, {})[citation.entry_id] = changed[
                node.node_id
            ]
        for artifact_id, entries in memory_changes.items():
            await self._set_memory_entries(artifact_id, entries, event.reason)
        await self._store.complete_event(
            self.scope_id,
            StoredLifecycleEvent(
                node_id=event.node_id,
                sequence=event.sequence,
                revision=event.revision,
                available=event.available,
                reason=event.reason,
                status=PENDING_EVENT,
            ),
            changed,
        )
        return state

    async def _recover_pending(
        self,
        pending: list[StoredLifecycleEvent],
    ) -> tuple[tuple[_Node, ...], tuple[StoredLifecycleEvent, ...]]:
        """Finish propagation for events whose native writes were interrupted.

        Readers never recover: until the event's own retry (or the next
        unrelated write) completes it, the durable event stays pending and the
        applied state is the one actually persisted.
        """

        for stored in pending:
            nodes, _ = await self._load()
            source = next((node for node in nodes if node.node_id == stored.node_id), None)
            if source is None:
                continue
            await self._propagate(source, _event_model(stored), nodes)
        return await self._load()

    # --------------------------------------------------------------------- resolve

    async def resolve(
        self,
        node_id: str,
        purpose: str = GENERATION_PURPOSE,
    ) -> SourceRef | MemoryCitation | ArtifactRef:
        """Return the original reference under the requested use purpose."""

        selected = _require_node_id(node_id)
        if not isinstance(purpose, str) or purpose not in _PURPOSES:
            raise SourceLifecycleError("purpose")
        async with self._runtime._scoped_operation(self.scope_id):
            nodes, _ = await self._load()
        node = next((item for item in nodes if item.node_id == selected), None)
        if node is None:
            raise SourceLifecycleError("node-missing", f"node {selected!r} is not registered")
        if purpose == GENERATION_PURPOSE and not node.available:
            raise SourceLifecycleError("unavailable", f"node {selected!r} is withdrawn")
        return node.reference

    # ---------------------------------------------------------------------- native

    async def _apply_native(self, node: _Node, available: bool, *, reason: str | None) -> None:
        if node.kind != MEMORY_NODE:
            # Topic visibility is projected from the durable derived state; Sources
            # have no native projection of their own.
            return
        citation = node.reference
        if not isinstance(citation, MemoryCitation):  # pragma: no cover - kind invariant
            return
        await self._set_memory_entries(
            citation.memory_ref.artifact_id,
            {citation.entry_id: available},
            reason,
        )

    async def _set_memory_entries(
        self,
        artifact_id: str,
        entries: Mapping[str, bool],
        reason: str | None,
    ) -> None:
        """Set the native active/inactive state of entries in one Memory artifact.

        All transitions of one event touching the same artifact are written in
        one Memory revision, so an interrupted native write never publishes a
        partially updated entry set.
        """

        async with self._memory_service() as service:
            try:
                memory = await service.head(artifact_id)
            except Exception as error:
                raise SourceLifecycleError("memory-missing", artifact_id) from error
            manifest = {item.entry_id: item.state for item in memory.content.manifest.entries}
            missing = [entry_id for entry_id in entries if entry_id not in manifest]
            if missing:
                raise SourceLifecycleError("entry-missing", missing[0])
            targets = {
                entry_id: available
                for entry_id, available in entries.items()
                if (manifest[entry_id] == "active") != available
            }
            if not targets:
                return
            versions = {item.entry_id: item for item in await service.entries(memory)}
            unavailable = [entry_id for entry_id in targets if entry_id not in versions]
            if unavailable:
                raise SourceLifecycleError("entry-missing", unavailable[0])
            native_reason = _native_reason(reason)
            to_deactivate = tuple(
                versions[entry_id] for entry_id, available in targets.items() if not available
            )
            to_activate = tuple(versions[entry_id] for entry_id, available in targets.items() if available)
            if to_deactivate:
                memory = await service.forget(memory, entries=to_deactivate, reason=native_reason)
            if to_activate:
                await service.reactivate(memory, entries=to_activate, reason=native_reason)

    @asynccontextmanager
    async def _memory_service(self) -> AsyncIterator[MemoryService]:
        async with self._runtime._context(self.scope_id) as context:
            yield context.artifacts.memory

    async def _require_readable(
        self,
        kind: str,
        reference: SourceRef | MemoryCitation | ArtifactRef,
    ) -> None:
        if kind == SOURCE_NODE:
            assert isinstance(reference, SourceRef)
            records = self._runtime._records()
            try:
                await records.get_source(self.scope_id, reference.source_type, reference.source_id)
            except Exception as error:
                raise SourceLifecycleError("unreadable-source", str(reference)) from error
            return
        if kind == MEMORY_NODE:
            assert isinstance(reference, MemoryCitation)
            async with self._memory_service() as service:
                try:
                    await service.revision(reference.memory_ref)
                    await service.validate_citation(reference)
                except Exception as error:
                    raise SourceLifecycleError("unreadable-memory", str(reference)) from error
            return
        assert isinstance(reference, ArtifactRef)
        records = self._runtime._records()
        try:
            await records.get_artifact_revision(
                self.scope_id,
                reference.family,
                reference.artifact_id,
                reference.revision,
            )
        except Exception as error:
            raise SourceLifecycleError("unreadable-topic", str(reference)) from error

    # ----------------------------------------------------------------------- state

    async def _load(self) -> tuple[tuple[_Node, ...], tuple[StoredLifecycleEvent, ...]]:
        stored_nodes, events = await self._store.load(self.scope_id)
        return tuple(self._node(stored) for stored in stored_nodes), events

    def _node(self, stored: StoredLifecycleNode) -> _Node:
        try:
            if stored.kind == SOURCE_NODE:
                reference: SourceRef | MemoryCitation | ArtifactRef = SourceRef.model_validate_json(
                    stored.payload
                )
            elif stored.kind == MEMORY_NODE:
                reference = MemoryCitation.model_validate_json(stored.payload)
            else:
                reference = ArtifactRef.model_validate_json(stored.payload)
        except ValidationError as error:  # pragma: no cover - payloads are written by this module
            raise SourceLifecycleError("node-conflict", stored.node_id) from error
        return _Node(
            node_id=stored.node_id,
            kind=stored.kind,
            order=stored.registration_order,
            policy=stored.policy,
            parents=stored.parents,
            reference=reference,
            available=stored.available,
            revision=stored.lifecycle_revision,
            sequence=stored.last_sequence,
        )


def _require_node_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_SOURCE_ID_LENGTH:
        raise SourceLifecycleError("node-id")
    return value


def _require_positive_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SourceLifecycleError(code)
    return value


def _require_policy(value: object) -> str:
    if not isinstance(value, str) or value not in _POLICIES:
        raise SourceLifecycleError("policy")
    return value


def _require_parents(node_id: str, parents: Sequence[str]) -> tuple[str, ...]:
    if isinstance(parents, str):
        raise SourceLifecycleError("parents")
    try:
        values = tuple(parents)
    except TypeError as error:
        raise SourceLifecycleError("parents") from error
    if not values:
        raise SourceLifecycleError("parents")
    for parent in values:
        if not isinstance(parent, str) or not parent.strip():
            raise SourceLifecycleError("parents")
        if parent == node_id:
            raise SourceLifecycleError("parent-self")
    if len(set(values)) != len(values):
        raise SourceLifecycleError("parent-duplicate")
    return values


def _support(parents: Sequence[str], policy: str, states: Mapping[str, bool]) -> bool:
    """Derive one node from its parents' current availability under ``all``/``any``."""

    support = [states[parent] for parent in parents]
    return all(support) if policy == "all" else any(support)


def _require_event(event: object) -> SourceLifecycleEvent:
    if isinstance(event, SourceLifecycleEvent):
        return event
    if isinstance(event, Mapping):
        try:
            return SourceLifecycleEvent.model_validate(dict(event))
        except ValidationError as error:
            raise SourceLifecycleError("event", str(error)) from error
    raise SourceLifecycleError("event")


def _event_model(stored: StoredLifecycleEvent) -> SourceLifecycleEvent:
    return SourceLifecycleEvent(
        node_id=stored.node_id,
        revision=stored.revision,
        sequence=stored.sequence,
        available=stored.available,
        reason=stored.reason,
    )


def _same_event(stored: StoredLifecycleEvent, selected: SourceLifecycleEvent) -> bool:
    return (
        stored.node_id == selected.node_id
        and stored.sequence == selected.sequence
        and stored.revision == selected.revision
        and stored.available == selected.available
        and stored.reason == selected.reason
    )


def _active_ids(state: Mapping[str, bool]) -> list[str]:
    return sorted(node_id for node_id, available in state.items() if available)


def _native_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    trimmed = reason.strip()
    if not trimmed:
        return None
    return trimmed[:_MAX_NATIVE_REASON_LENGTH]


__all__ = [
    "AUDIT_PURPOSE",
    "GENERATION_PURPOSE",
    "ScopedSourceLifecycle",
    "SourceLifecycleApplication",
    "SourceLifecycleError",
    "SourceLifecycleEvent",
    "SourceLifecycleStore",
]
