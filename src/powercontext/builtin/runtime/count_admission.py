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

"""Count-driven admission of the ordinary bounded Source processing call.

Automatic processing already starts at a time condition. This module adds the
independent count condition: once the persisted journal holds at least the
configured number of identities behind a Family's last committed Source cursor,
the Runtime accepts exactly one ordinary processing invocation for that Scope.

Admission only publishes durable Intent counters, so the existing Supervisor
owns dispatch, retries, and recovery exactly as it does for a scheduled or
explicitly flushed invocation. Acceptance is deliberately edge-triggered by the
cursor: a window that leaves fewer identities than the threshold stops and waits
for new input, a time condition, or an explicit flush.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from contextlib import suppress

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext._logging import log_safely
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.tables import SOURCE_JOURNAL_HEADS_TABLE

logger = logging.getLogger(__name__)

DEFAULT_SOURCE_COUNT_POLL_SECONDS = 0.25
_SOURCE_COUNT_PAGE_SIZE = 100
_SOURCE_COUNT_CLOSE_TIMEOUT_SECONDS = 5.0


class SourceCountAdmission:
    """Accept one processing invocation per Scope when its pending count is due.

    The candidate set is the durable per-Binding dirty Intent set, which every
    newly persisted journal identity updates inside the capture transaction. The
    count itself is the persisted journal head minus the last successfully
    committed Source cursor, so replayed captures, receipt trust upgrades, and
    already consumed windows never add pending work.
    """

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        thresholds: Mapping[str, int],
        wake: Callable[[str], None],
        poll_seconds: float = DEFAULT_SOURCE_COUNT_POLL_SECONDS,
        page_size: int = _SOURCE_COUNT_PAGE_SIZE,
    ) -> None:
        if not thresholds:
            raise ValueError("count admission requires at least one threshold")  # noqa: TRY003
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")  # noqa: TRY003
        if type(page_size) is not int or page_size < 1:
            raise ValueError("page_size must be a positive integer")  # noqa: TRY003
        validated: dict[str, int] = {}
        for binding_name, threshold in thresholds.items():
            if not isinstance(binding_name, str) or not binding_name.strip():
                raise ValueError("binding_name must be a non-empty identifier")  # noqa: TRY003
            if type(threshold) is not int or threshold < 1:
                raise ValueError("threshold must be a positive integer")  # noqa: TRY003
            validated[binding_name] = threshold
        self._database = database
        self._thresholds = validated
        self._wake = wake
        self._poll_seconds = poll_seconds
        self._page_size = page_size
        self._intents = ArtifactProcessingIntentRepository()
        self._cursors = SourceCursorRepository()
        self._after: dict[str, int] = dict.fromkeys(validated, 0)
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def thresholds(self) -> Mapping[str, int]:
        return dict(self._thresholds)

    async def __aenter__(self) -> SourceCountAdmission:
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        await self.close()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run(), name="source-count-admission")

    async def close(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=_SOURCE_COUNT_CLOSE_TIMEOUT_SECONDS)
        except TimeoutError:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log_safely(
                logger,
                logging.WARNING,
                "Source count admission stopped with an error",
                exc_info=error,
                extra={"event": "source_count.admission.stopped", "outcome": "failure", "unit": "background"},
            )

    async def run(self) -> None:
        """Evaluate every configured Family until the Runtime closes."""

        while not self._stop.is_set():
            for binding_name in self._thresholds:
                if self._stop.is_set():
                    break
                try:
                    await self._evaluate(binding_name)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    # Admission is an optimization of the existing scheduler. A
                    # transient database error must not permanently stop future
                    # attempts, and it never consumes accepted work.
                    log_safely(
                        logger,
                        logging.WARNING,
                        "Source count admission failed",
                        exc_info=error,
                        extra={
                            "event": "source_count.admission.failed",
                            "outcome": "failure",
                            "unit": "background",
                            "binding": binding_name,
                        },
                    )
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_seconds)

    async def _evaluate(self, binding_name: str) -> None:
        threshold = self._thresholds[binding_name]
        candidates = await self._candidates(binding_name, threshold)
        if not candidates:
            return
        accepted = False
        for scope_id in candidates:
            if await self._accept(scope_id, binding_name, threshold):
                accepted = True
        if accepted:
            self._wake(binding_name)

    async def _candidates(self, binding_name: str, threshold: int) -> list[str]:
        """Read one bounded page of dirty Intents and select due Scopes."""

        after = self._after[binding_name]
        candidates: list[str] = []
        async with self._database.transaction() as connection:
            rows = await self._intents.scan(
                connection,
                binding_name,
                after_sequence=after,
                limit=self._page_size,
                # Count every registered Scope: a failed processing attempt may
                # consume the dirty flag without advancing the successful cursor,
                # and the pending input must still trigger again after a restart.
                dirty_only=False,
            )
            for row in rows:
                # Accepted but unhandled work already owns the next invocation;
                # re-requesting here would run a second window before the
                # threshold is judged again against the new Cursor.
                if row.requested_generation > row.handled_generation:
                    continue
                if await self._pending_count(connection, row.scope_id, binding_name) >= threshold:
                    candidates.append(row.scope_id)
        self._after[binding_name] = 0 if len(rows) < self._page_size else rows[-1].pending_sequence
        return candidates

    async def _accept(self, scope_id: str, binding_name: str, threshold: int) -> bool:
        """Publish one accepted invocation, re-validating under a lock."""

        async with self._database.transaction() as connection:
            # Lock the journal head before the Intent, matching Source capture's
            # order. It also makes SQLite open the real write transaction first.
            await connection.execute(
                update(SOURCE_JOURNAL_HEADS_TABLE)
                .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
                .values(position=SOURCE_JOURNAL_HEADS_TABLE.c.position)
            )
            intent = await self._intents.load(connection, scope_id, binding_name, for_update=True)
            if intent is None or intent.requested_generation > intent.handled_generation:
                return False
            if await self._pending_count(connection, scope_id, binding_name) < threshold:
                return False
            await self._intents.request(connection, scope_id, binding_name)
        return True

    async def _pending_count(self, connection: AsyncConnection, scope_id: str, binding_name: str) -> int:
        """Count journal identities after the Family's last committed Cursor."""

        head = await connection.scalar(
            select(SOURCE_JOURNAL_HEADS_TABLE.c.position).where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
        )
        if head is None:
            return 0
        cursor = await self._cursors.load(connection, scope_id, binding_name)
        consumed = 0 if cursor is None else cursor.cursor.sequence
        return max(0, int(head) - consumed)


__all__ = ["DEFAULT_SOURCE_COUNT_POLL_SECONDS", "SourceCountAdmission"]
