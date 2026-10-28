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

"""Durable registrations, events, and derived availability for Source Lifecycle.

The store owns only relational state. Dependency derivation, reference
resolution, and the native Memory/Topic effects belong to the Runtime service,
which lets one event failure leave the durable event pending while the same
event retried later completes the propagation.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import false, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import (
    SOURCE_LIFECYCLE_EVENTS_TABLE,
    SOURCE_LIFECYCLE_NODES_TABLE,
)

SOURCE_NODE = "source"
MEMORY_NODE = "memory"
TOPIC_NODE = "topic"

PENDING_EVENT = "pending"
APPLIED_EVENT = "applied"


@dataclass(frozen=True, slots=True)
class StoredLifecycleNode:
    """One registered evidence node in registration (dependency) order."""

    node_id: str
    kind: str
    registration_order: int
    payload: str
    policy: str | None
    parents: tuple[str, ...]
    available: bool
    lifecycle_revision: int
    last_sequence: int


@dataclass(frozen=True, slots=True)
class StoredLifecycleEvent:
    """One accepted Source event and whether propagation already committed."""

    node_id: str
    sequence: int
    revision: int
    available: bool
    reason: str
    status: str


class RelationalSourceLifecycleStore:
    """Read and write one scope's Source Lifecycle records through SQLAlchemy."""

    def __init__(self, database: AsyncDatabase) -> None:
        self.database = database

    async def load(
        self,
        scope_id: str,
        /,
    ) -> tuple[tuple[StoredLifecycleNode, ...], tuple[StoredLifecycleEvent, ...]]:
        async with self.database.transaction() as connection:
            return await self.load_with(connection, scope_id)

    async def load_with(
        self,
        connection: AsyncConnection,
        scope_id: str,
        /,
    ) -> tuple[tuple[StoredLifecycleNode, ...], tuple[StoredLifecycleEvent, ...]]:
        node_rows = (
            await connection.execute(
                select(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.node_id,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.kind,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.registration_order,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.payload,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.policy,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.parents,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.available,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.lifecycle_revision,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.last_sequence,
                )
                .where(SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id)
                .order_by(SOURCE_LIFECYCLE_NODES_TABLE.c.registration_order)
            )
        ).mappings()
        event_rows = (
            await connection.execute(
                select(
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.node_id,
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.sequence,
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.revision,
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.available,
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.reason,
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.status,
                )
                .where(SOURCE_LIFECYCLE_EVENTS_TABLE.c.scope_id == scope_id)
                .order_by(
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.node_id,
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.sequence,
                )
            )
        ).mappings()
        nodes = tuple(
            StoredLifecycleNode(
                node_id=str(row["node_id"]),
                kind=str(row["kind"]),
                registration_order=int(row["registration_order"]),
                payload=str(row["payload"]),
                policy=None if row["policy"] is None else str(row["policy"]),
                parents=tuple(str(parent) for parent in json.loads(str(row["parents"]))),
                available=bool(row["available"]),
                lifecycle_revision=int(row["lifecycle_revision"]),
                last_sequence=int(row["last_sequence"]),
            )
            for row in node_rows
        )
        events = tuple(
            StoredLifecycleEvent(
                node_id=str(row["node_id"]),
                sequence=int(row["sequence"]),
                revision=int(row["revision"]),
                available=bool(row["available"]),
                reason=str(row["reason"]),
                status=str(row["status"]),
            )
            for row in event_rows
        )
        return nodes, events

    async def insert_node(self, scope_id: str, node: StoredLifecycleNode, /) -> None:
        async with self.database.transaction() as connection:
            await connection.execute(
                insert(SOURCE_LIFECYCLE_NODES_TABLE).values(
                    scope_id=scope_id,
                    node_id=node.node_id,
                    registration_order=node.registration_order,
                    kind=node.kind,
                    payload=node.payload,
                    policy=node.policy,
                    parents=json.dumps(list(node.parents)),
                    available=node.available,
                    lifecycle_revision=node.lifecycle_revision,
                    last_sequence=node.last_sequence,
                )
            )

    async def insert_event(self, scope_id: str, event: StoredLifecycleEvent, /) -> None:
        async with self.database.transaction() as connection:
            await connection.execute(
                insert(SOURCE_LIFECYCLE_EVENTS_TABLE).values(
                    scope_id=scope_id,
                    node_id=event.node_id,
                    sequence=event.sequence,
                    revision=event.revision,
                    available=event.available,
                    reason=event.reason,
                    status=event.status,
                )
            )

    async def complete_event(
        self,
        scope_id: str,
        event: StoredLifecycleEvent,
        availability: Mapping[str, bool],
        /,
    ) -> None:
        """Persist the propagated state and mark the event applied atomically.

        Native effects run before this call, so a failure while writing them
        leaves both the derived availability and the event status untouched and
        the same event can still be retried.
        """

        async with self.database.transaction() as connection:
            for node_id, value in availability.items():
                await connection.execute(
                    update(SOURCE_LIFECYCLE_NODES_TABLE)
                    .where(
                        SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                        SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node_id,
                    )
                    .values(available=value)
                )
            await connection.execute(
                update(SOURCE_LIFECYCLE_NODES_TABLE)
                .where(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == event.node_id,
                )
                .values(last_sequence=event.sequence)
            )
            await connection.execute(
                update(SOURCE_LIFECYCLE_EVENTS_TABLE)
                .where(
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.scope_id == scope_id,
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.node_id == event.node_id,
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.sequence == event.sequence,
                )
                .values(status=APPLIED_EVENT)
            )

    async def set_node_available(self, scope_id: str, node_id: str, available: bool, /) -> None:
        async with self.database.transaction() as connection:
            await connection.execute(
                update(SOURCE_LIFECYCLE_NODES_TABLE)
                .where(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node_id,
                )
                .values(available=available)
            )

    async def hidden_topic_artifacts(self, scope_id: str, /) -> frozenset[str]:
        async with self.database.transaction() as connection:
            return await self.hidden_topic_artifacts_with(connection, scope_id)

    async def hidden_topic_artifacts_with(
        self,
        connection: AsyncConnection,
        scope_id: str,
        /,
    ) -> frozenset[str]:
        """Return Topic artifacts whose registered support is currently withdrawn."""

        rows = (
            await connection.execute(
                select(SOURCE_LIFECYCLE_NODES_TABLE.c.payload).where(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.kind == TOPIC_NODE,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.available == false(),
                )
            )
        ).scalars()
        hidden: set[str] = set()
        for payload in rows:
            try:
                reference = json.loads(str(payload))
            except ValueError:  # pragma: no cover - payloads are written as JSON
                continue
            artifact_id = reference.get("artifact_id")
            if isinstance(artifact_id, str):
                hidden.add(artifact_id)
        return frozenset(hidden)


__all__ = [
    "APPLIED_EVENT",
    "MEMORY_NODE",
    "PENDING_EVENT",
    "RelationalSourceLifecycleStore",
    "SOURCE_NODE",
    "StoredLifecycleEvent",
    "StoredLifecycleNode",
    "TOPIC_NODE",
]
