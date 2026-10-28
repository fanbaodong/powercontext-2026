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

"""HTTP cross-encoder reranking over the complete coarse candidate pool."""

from __future__ import annotations

import math
from typing import Any

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import (
    MemoryRerankDecision,
    _validate_bounds,
)
from powercontext.builtin.inference import InferenceUsage


class HTTPMemoryReranker:
    """Score Memory candidates through one HTTP reranking service.

    Candidates are split into bounded batches while keeping their original global
    rank.  The service scores are merged into one global ordering, so a high score
    from a later batch still wins, and the selected ranks keep pointing at the
    original Memory hits and versions.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("rerank endpoint must be a non-empty URL")
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError("rerank batch_size must be a positive integer")
        if not isinstance(policy_id, str) or not policy_id.strip():
            raise ValueError("rerank policy_id must be a non-empty string")
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Score every candidate once and return the globally best original ranks."""

        _validate_bounds(len(candidates), limit)
        scores: dict[int, float] = {}
        request_count = 0
        for offset in range(0, len(candidates), self._batch_size):
            batch = candidates[offset : offset + self._batch_size]
            expected_ranks = tuple(offset + index + 1 for index in range(len(batch)))
            payload = {
                "query": query,
                "candidates": [
                    {"rank": rank, "text": candidate.text}
                    for rank, candidate in zip(expected_ranks, batch, strict=True)
                ],
            }
            response = await self._client.post(self._endpoint, json=payload)
            response.raise_for_status()
            request_count += 1
            scores.update(_parse_batch_scores(response, expected_ranks))

        selected = tuple(sorted(scores, key=lambda rank: (-scores[rank], rank))[:limit])
        return MemoryRerankDecision(
            selected_ranks=selected,
            usage=InferenceUsage(requests=request_count),
        )


def _parse_batch_scores(
    response: httpx.Response,
    expected_ranks: tuple[int, ...],
) -> dict[int, float]:
    """Validate one response so it covers exactly this batch's global ranks."""

    payload: Any = response.json()
    if not isinstance(payload, dict) or set(payload) != {"scores"}:
        raise ValueError("rerank response must be a JSON object with exactly the scores field")
    entries = payload["scores"]
    if not isinstance(entries, list):
        raise ValueError("rerank response scores must be a JSON array")

    scores: dict[int, float] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"rank", "score"}:
            raise ValueError("rerank response entries must contain exactly rank and score")
        rank = entry["rank"]
        score = entry["score"]
        if type(rank) is not int or rank not in expected_ranks:
            raise ValueError("rerank response rank must be one of the requested global ranks")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("rerank response score must be a number")
        value = float(score)
        if not math.isfinite(value):
            raise ValueError("rerank response score must be finite")
        if rank in scores:
            raise ValueError("rerank response must not repeat a rank")
        scores[rank] = value

    if set(scores) != set(expected_ranks):
        raise ValueError("rerank response must cover every requested rank exactly once")
    return scores


__all__ = ["HTTPMemoryReranker"]
