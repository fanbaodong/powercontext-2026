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

"""Cross-call Embedding request coalescing with a bounded success-vector cache.

:class:`CoalescingEmbeddingModel` wraps one deployment-fixed Embedding model and
lets concurrent callers in a single event loop share identical texts. A caller
still receives one vector per original input position, in the original order and
with duplicate positions preserved; its usage is summed over the distinct
underlying batches it waited for, so a shared batch is charged once per waiter
and a pure cache hit costs no request.

Only successful vectors are cached, bounded by an LRU capacity. Cancelling one
caller never cancels the underlying batch it shares with other callers, and a
failing batch reaches exactly the callers waiting for it without poisoning the
cache or unrelated batches.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Iterable
from typing import TYPE_CHECKING

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage

if TYPE_CHECKING:
    from powercontext.builtin.inference.protocols import EmbeddingModel

_DEFAULT_MAX_BATCH_SIZE = 64
_DEFAULT_COALESCE_MS = 5.0
_DEFAULT_CACHE_SIZE = 1024


class _Batch:
    """One pending or running underlying request shared by every interested caller.

    ``texts`` holds only distinct values: a batch is built from texts that no other
    pending or running batch owns yet, so one text is sent to the model once while
    at least one request for it is outstanding.
    """

    __slots__ = ("future", "texts")

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.future: asyncio.Future[EmbeddingResult] = asyncio.get_running_loop().create_future()
        # A batch whose callers were all cancelled nobody awaits, so retrieve its
        # outcome here; awaiting the future later still raises or returns it.
        self.future.add_done_callback(_retrieve_exception)


def _retrieve_exception(future: asyncio.Future[EmbeddingResult]) -> None:
    if not future.cancelled():
        future.exception()


class CoalescingEmbeddingModel:
    """Share identical Embedding inputs across concurrent callers and cache successes.

    The wrapper keeps the wrapped model's ``profile`` and returns the existing
    :class:`~powercontext.builtin.inference.models.EmbeddingResult`, so it is a
    drop-in ``EmbeddingModel`` for the built-in runtime and Memory service.
    """

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = _DEFAULT_MAX_BATCH_SIZE,
        coalesce_ms: float = _DEFAULT_COALESCE_MS,
        cache_size: int = _DEFAULT_CACHE_SIZE,
    ) -> None:
        if not _is_integer(max_batch_size) or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")
        if isinstance(coalesce_ms, bool) or not isinstance(coalesce_ms, (int, float)) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be a non-negative number")
        if not _is_integer(cache_size) or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")
        self._model = model
        self.profile = model.profile
        self.max_batch_size = max_batch_size
        self.coalesce_ms = float(coalesce_ms)
        self.cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        # Texts owned by a pending or running batch, so late callers can join it
        # instead of paying for a second request of the same text.
        self._inflight: dict[str, _Batch] = {}
        self._pending: list[_Batch] = []
        self._flush_handle: asyncio.Handle | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Return one vector per input position, sharing identical text with other callers."""

        ordered = tuple(texts)
        if not ordered:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))
        unique = tuple(dict.fromkeys(ordered))
        resolved: dict[str, EmbeddingVector] = {}
        waits: list[_Batch] = []
        attached: set[int] = set()
        missing: list[str] = []
        # Everything below runs without awaiting, so a text is either cached, joined
        # to one batch, or scheduled exactly once for this caller.
        for text in unique:
            cached = self._cache_get(text)
            if cached is not None:
                resolved[text] = cached
                continue
            batch = self._inflight.get(text)
            if batch is None:
                missing.append(text)
                continue
            if id(batch) not in attached:
                attached.add(id(batch))
                waits.append(batch)
        if missing:
            self._enqueue(missing, waits, attached)
        usages: list[InferenceUsage] = []
        for batch in waits:
            # Shield the shared batch: cancelling this caller must leave the work other
            # callers depend on running to completion.
            result = await asyncio.shield(batch.future)
            usages.append(result.usage)
            for text, vector in zip(batch.texts, result.vectors, strict=True):
                resolved[text] = vector
        return EmbeddingResult(
            vectors=tuple(resolved[text] for text in ordered),
            usage=_merge_usage(usages),
        )

    def _enqueue(self, texts: list[str], waits: list[_Batch], attached: set[int]) -> None:
        """Pack new texts into the open batch, opening another only past the limit."""

        for text in texts:
            batch = self._pending[-1] if self._pending else None
            if batch is None or len(batch.texts) >= self.max_batch_size:
                batch = _Batch()
                self._pending.append(batch)
            batch.texts.append(text)
            self._inflight[text] = batch
            if id(batch) not in attached:
                attached.add(id(batch))
                waits.append(batch)
        self._schedule_flush()

    def _schedule_flush(self) -> None:
        if self._flush_handle is not None:
            return
        loop = asyncio.get_running_loop()
        if self.coalesce_ms > 0:
            self._flush_handle = loop.call_later(self.coalesce_ms / 1_000, self._flush)
        else:
            self._flush_handle = loop.call_soon(self._flush)

    def _flush(self) -> None:
        """Start one independent task per batch once the merging window closes."""

        self._flush_handle = None
        pending = self._pending
        self._pending = []
        if not pending:
            return
        loop = asyncio.get_running_loop()
        for batch in pending:
            task = loop.create_task(self._run_batch(batch))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run_batch(self, batch: _Batch) -> None:
        try:
            result = await self._model.embed(tuple(batch.texts))
        except asyncio.CancelledError as error:
            self._fail_batch(batch, error)
            raise
        except Exception as error:  # every provider failure reaches the callers waiting for it
            self._fail_batch(batch, error)
            return
        vectors = tuple(result.vectors)
        if len(vectors) != len(batch.texts):
            self._fail_batch(
                batch,
                ValueError(f"embedding model returned {len(vectors)} vectors for {len(batch.texts)} texts"),
            )
            return
        for text, vector in zip(batch.texts, vectors, strict=True):
            self._cache_put(text, vector)
        self._release(batch)
        if not batch.future.done():
            batch.future.set_result(result)

    def _fail_batch(self, batch: _Batch, error: BaseException) -> None:
        # A failed batch is not cached, so its texts become retryable immediately.
        self._release(batch)
        if not batch.future.done():
            batch.future.set_exception(error)

    def _release(self, batch: _Batch) -> None:
        for text in batch.texts:
            if self._inflight.get(text) is batch:
                del self._inflight[text]

    def _cache_get(self, text: str) -> EmbeddingVector | None:
        if not self._cache:
            return None
        vector = self._cache.get(text)
        if vector is None:
            return None
        self._cache.move_to_end(text)
        return vector

    def _cache_put(self, text: str, vector: EmbeddingVector) -> None:
        if self.cache_size == 0:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _merge_usage(usages: list[InferenceUsage]) -> InferenceUsage:
    """Sum the usage of the distinct batches this caller actually waited for."""

    if not usages:
        return InferenceUsage(requests=0)
    return InferenceUsage(
        requests=sum(usage.requests for usage in usages),
        input_tokens=_sum_tokens(usage.input_tokens for usage in usages),
        output_tokens=_sum_tokens(usage.output_tokens for usage in usages),
    )


def _sum_tokens(values: Iterable[int | None]) -> int | None:
    observed = [value for value in values if value is not None]
    return sum(observed) if observed else None


__all__ = ["CoalescingEmbeddingModel"]
