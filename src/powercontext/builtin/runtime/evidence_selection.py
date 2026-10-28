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

"""Select query-relevant literal evidence from recalled Memory entries.

The selector never rewrites evidence: every returned fragment is an exact
``entry.text[start:end]`` character interval together with the address of the entry version it
was read from. Selection is intentionally pure and local: it only sees the query and the
already recalled Memory candidates, so the same inputs always produce the same fragments.

The byte ceiling covers the complete rendered envelope (trust policy, markers, JSON, citation
metadata and content), not just the fragment bodies. Candidate states are therefore costed with
the same renderer that produces the delivered output, and an addition is only accepted when the
fully serialized result still fits ``max_bytes``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

from powercontext.artifacts import ArtifactAddress
from powercontext.builtin.artifacts.search import analyze_text_with_spans
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY

BEGIN_MARKER = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
END_MARKER = "END_POWERCONTEXT_PREPARED_CONTEXT_V1"

_SENTENCE_ENDINGS = frozenset(".!?")
_TRAILING_CLOSERS = frozenset("\"'”’)]}")
_ABBREVIATIONS = frozenset({
    "al",
    "approx",
    "ca",
    "cf",
    "co",
    "corp",
    "dr",
    "eq",
    "eqs",
    "et",
    "fig",
    "figs",
    "inc",
    "jr",
    "ltd",
    "max",
    "min",
    "mr",
    "mrs",
    "ms",
    "no",
    "nos",
    "prof",
    "ref",
    "refs",
    "resp",
    "sec",
    "sr",
    "st",
    "tab",
    "vol",
    "vs",
    "wt",
})
_MIN_PREFIX_MATCH = 5
_MAX_PREFIX_GROWTH = 4
_PHRASE_BONUS = 0.5
_RANK_PRIOR = 0.04
_COVERAGE_FLOOR = 0.45
_MAX_FRAGMENTS = 8
_POSITION_PRIOR_WEIGHT = 8.0
_POSITION_PRIOR_EXPONENT = 2.0


@dataclass(frozen=True)
class MemoryEntryAddress:
    """Identify one exact Memory entry version across Scope boundaries."""

    memory: ArtifactAddress
    entry_id: str
    entry_version_id: str


@dataclass(frozen=True)
class EvidenceFragment:
    """One literal slice of an exact Memory entry version."""

    address: MemoryEntryAddress
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class EvidenceCandidate:
    """One recalled Memory entry, in retrieval rank order."""

    address: MemoryEntryAddress
    text: str


@dataclass(frozen=True)
class _Sentence:
    index: int
    start: int
    end: int


@dataclass(frozen=True)
class _EntryPlan:
    rank: int
    candidate: EvidenceCandidate
    sentences: tuple[_Sentence, ...]
    scores: tuple[float, ...]
    best_index: int
    best_score: float
    entry_score: float


@dataclass(frozen=True)
class _Run:
    """A maximal contiguous run of selected sentences inside one entry."""

    first: int
    last: int


@dataclass(frozen=True)
class _Hypothetical:
    runs: tuple[_Run, ...]
    item_bytes: tuple[int, ...]
    fragments: tuple[EvidenceFragment, ...]
    total_bytes: int


def render_evidence_context(fragments: Sequence[EvidenceFragment]) -> str:
    """Render selected fragments as the prepared-context envelope.

    The envelope keeps the established trust policy and BEGIN/END markers. Each item carries
    the exact citation identity of the entry version the slice was read from, together with the
    half-open character interval and the literal slice text.
    """

    envelope = {
        "trust": "untrusted_history",
        "items": [_render_item(fragment) for fragment in fragments],
    }
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return "\n\n".join((TRUST_POLICY, f"{BEGIN_MARKER}\n{encoded}\n{END_MARKER}"))


def evidence_output_bytes(fragments: Sequence[EvidenceFragment]) -> int:
    """Return the exact UTF-8 byte length of the complete rendered output."""

    return len(render_evidence_context(fragments).encode("utf-8"))


_EMPTY_OUTPUT_BYTES = evidence_output_bytes(())


def select_evidence_fragments(
    query: str,
    candidates: Sequence[EvidenceCandidate],
    *,
    max_bytes: int,
    fragment_limit: int = _MAX_FRAGMENTS,
) -> tuple[EvidenceFragment, ...]:
    """Choose literal fragments from recalled entries for one query and byte budget."""

    if not candidates or max_bytes <= _EMPTY_OUTPUT_BYTES or fragment_limit < 1:
        return ()
    plans = _build_plans(query, candidates)
    if not plans:
        return ()
    selection = _Selection(plans, max_bytes=max_bytes, fragment_limit=fragment_limit)
    if any(plan.best_score > 0.0 for plan in plans):
        _select_windows(selection, plans)
    else:
        # No sentence shares a query term: still deliver the head of the best-ranked entry
        # rather than an empty context, and let the budget fill the rest of that entry.
        selection.try_add(plans[0].rank, 0)
    _fill_adjacent(selection)
    fragments = selection.fragments()
    # The incremental cost model is exact, but the delivered bytes remain the authority.
    while fragments and evidence_output_bytes(fragments) > max_bytes:
        fragments = fragments[:-1]
    return fragments


def _render_item(fragment: EvidenceFragment) -> dict[str, object]:
    return {
        "citation": {
            "scope_id": fragment.address.memory.scope_id,
            "memory_ref": fragment.address.memory.artifact.model_dump(mode="json"),
            "entry_id": fragment.address.entry_id,
            "entry_version_id": fragment.address.entry_version_id,
        },
        "content": fragment.text,
        "start": fragment.start,
        "end": fragment.end,
    }


def _item_json_bytes(fragment: EvidenceFragment) -> int:
    encoded = json.dumps(_render_item(fragment), ensure_ascii=False, separators=(",", ":"))
    return len(encoded.encode("utf-8"))


def _build_plans(query: str, candidates: Sequence[EvidenceCandidate]) -> tuple[_EntryPlan, ...]:
    spans_by_entry: list[tuple[tuple[int, int], ...]] = []
    positions_by_entry: list[list[dict[str, tuple[int, ...]]]] = []
    document_frequency: dict[str, int] = {}
    sentence_count = 0
    for candidate in candidates:
        spans = _sentence_spans(candidate.text)
        positions: list[dict[str, tuple[int, ...]]] = []
        for start, end in spans:
            stems = tuple(_stem(token) for token in _tokens(candidate.text[start:end]))
            stem_positions: dict[str, list[int]] = {}
            for position, stem in enumerate(stems):
                stem_positions.setdefault(stem, []).append(position)
            frozen = {stem: tuple(values) for stem, values in stem_positions.items()}
            positions.append(frozen)
            for stem in frozen:
                document_frequency[stem] = document_frequency.get(stem, 0) + 1
            sentence_count += 1
        spans_by_entry.append(spans)
        positions_by_entry.append(positions)

    query_stems = tuple(dict.fromkeys(_stem(token) for token in _tokens(query)))
    if not query_stems or sentence_count == 0:
        return ()
    weights = {
        term: max(0.05, math.log((sentence_count + 1.5) / (document_frequency.get(term, 0) + 0.5)))
        for term in query_stems
    }
    total_weight = sum(weights.values())
    query_pairs = tuple(zip(query_stems, query_stems[1:]))

    plans: list[_EntryPlan] = []
    for rank, candidate in enumerate(candidates):
        spans = spans_by_entry[rank]
        if not spans:
            continue
        scores = tuple(
            _sentence_score(
                rank=rank,
                query_stems=query_stems,
                query_pairs=query_pairs,
                weights=weights,
                total_weight=total_weight,
                positions=positions_by_entry[rank][index],
            )
            for index in range(len(spans))
        )
        best_index = max(range(len(scores)), key=lambda index: (scores[index], -index))
        entry_score = sum(sorted((score for score in scores if score > 0.0), reverse=True)[:3])
        sentences = tuple(
            _Sentence(index=index, start=start, end=end) for index, (start, end) in enumerate(spans)
        )
        plans.append(
            _EntryPlan(
                rank=rank,
                candidate=candidate,
                sentences=sentences,
                scores=scores,
                best_index=best_index,
                best_score=scores[best_index],
                entry_score=entry_score,
            )
        )
    return tuple(plans)


def _sentence_score(
    *,
    rank: int,
    query_stems: tuple[str, ...],
    query_pairs: tuple[tuple[str, str], ...],
    weights: dict[str, float],
    total_weight: float,
    positions: dict[str, tuple[int, ...]],
) -> float:
    matched: dict[str, set[int]] = {}
    matched_weight = 0.0
    for term in query_stems:
        term_positions = _term_positions(term, positions)
        if term_positions:
            matched[term] = term_positions
            matched_weight += weights[term]
    if not matched or total_weight <= 0.0:
        return 0.0
    coverage = matched_weight / total_weight
    score = matched_weight * (_COVERAGE_FLOOR + (1.0 - _COVERAGE_FLOOR) * coverage)
    for left, right in query_pairs:
        if left == right:
            continue
        left_positions = matched.get(left)
        right_positions = matched.get(right)
        if left_positions and right_positions and any(position + 1 in right_positions for position in left_positions):
            score += _PHRASE_BONUS * (weights[left] + weights[right])
    return score * (1.0 + _RANK_PRIOR / (rank + 1))


def _term_positions(term: str, positions: dict[str, tuple[int, ...]]) -> set[int]:
    exact = positions.get(term)
    merged: set[int] = set(exact) if exact is not None else set()
    if not merged:
        for stem, stem_positions in positions.items():
            if stem != term and _prefix_match(term, stem):
                merged.update(stem_positions)
    return merged


def _prefix_match(left: str, right: str) -> bool:
    if left == right:
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    return (
        len(shorter) >= _MIN_PREFIX_MATCH
        and longer.startswith(shorter)
        and len(longer) - len(shorter) <= _MAX_PREFIX_GROWTH
    )


def _tokens(value: str) -> tuple[str, ...]:
    """Tokenize with the same analyzer retrieval uses, so CJK queries stay query-driven."""

    return tuple(term for term, _start, _end in analyze_text_with_spans(value))


def _stem(token: str) -> str:
    """Fold common English inflections so claim wording matches abstract wording."""

    if len(token) <= 3:
        return token
    if token.endswith("ies") and len(token) > 4:
        token = f"{token[:-3]}y"
    elif token.endswith(("sses", "shes", "ches", "xes", "zes")):
        token = token[:-2]
    elif token.endswith("s") and not token.endswith(("ss", "us", "is")):
        token = token[:-1]
    if token.endswith("ing") and len(token) > 5:
        token = token[:-3]
    elif token.endswith("ed") and len(token) > 4:
        token = token[:-2]
    elif token.endswith("ly") and len(token) > 5:
        token = token[:-2]
    if token.endswith("ation") and len(token) > 8:
        token = token[:-3]
    return token


def _sentence_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Split text into sentences without losing exact character offsets.

    Splitting is deliberately conservative: an ambiguous period (abbreviation, initial, or a
    following lowercase word) stays inside the current sentence, so a selected interval can
    only be wider than the annotated sentence, never narrower.
    """

    spans: list[tuple[int, int]] = []
    length = len(text)
    start = _skip_space(text, 0)
    index = start
    while index < length:
        char = text[index]
        boundary: int | None = None
        if char in _SENTENCE_ENDINGS and _is_sentence_boundary(text, index):
            boundary = index + 1
            while boundary < length and text[boundary] in _TRAILING_CLOSERS:
                boundary += 1
        elif char == "\n":
            boundary = index
        if boundary is not None:
            end = boundary
            while end > start and text[end - 1].isspace():
                end -= 1
            if end > start:
                spans.append((start, end))
            start = _skip_space(text, boundary if boundary > index else index + 1)
            index = start
            continue
        index += 1
    if start < length:
        end = length
        while end > start and text[end - 1].isspace():
            end -= 1
        if end > start:
            spans.append((start, end))
    return tuple(spans)


def _skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _is_sentence_boundary(text: str, index: int) -> bool:
    length = len(text)
    following = index + 1
    while following < length and text[following] in _TRAILING_CLOSERS:
        following += 1
    if following < length and not text[following].isspace():
        return False
    word_start = index
    while word_start > 0 and text[word_start - 1].isalnum():
        word_start -= 1
    word = text[word_start:index].casefold()
    if text[index] == "." and ((len(word) == 1 and word.isalpha()) or word in _ABBREVIATIONS):
        return False
    next_index = _skip_space(text, following)
    return not (next_index < length and text[next_index].islower())


class _Selection:
    """Incremental selection state with exact serialized-byte accounting."""

    def __init__(self, plans: Sequence[_EntryPlan], *, max_bytes: int, fragment_limit: int) -> None:
        self._plans = {plan.rank: plan for plan in plans}
        self._plan_order = tuple(plan.rank for plan in plans)
        self._max_bytes = max_bytes
        self._fragment_limit = fragment_limit
        self._runs: dict[int, tuple[_Run, ...]] = {}
        self._item_bytes: dict[int, tuple[int, ...]] = {}
        self._item_total = 0
        self._item_count = 0
        self._item_cache: dict[tuple[str, str, int, int], int] = {}

    def fragments(self) -> tuple[EvidenceFragment, ...]:
        ordered: list[EvidenceFragment] = []
        for rank in self._plan_order:
            runs = self._runs.get(rank)
            if not runs:
                continue
            plan = self._plans[rank]
            ordered.extend(_fragment(plan, run) for run in runs)
        return tuple(ordered)

    def runs(self, rank: int) -> tuple[_Run, ...]:
        return self._runs.get(rank, ())

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    @property
    def item_count(self) -> int:
        return self._item_count

    @property
    def total_bytes(self) -> int:
        return self._rendered_bytes()

    def _rendered_bytes(self) -> int:
        return _EMPTY_OUTPUT_BYTES + self._item_total + max(0, self._item_count - 1)

    def plan_ranks(self) -> tuple[int, ...]:
        return tuple(rank for rank in self._plan_order if self._runs.get(rank))

    def covered(self, rank: int, index: int) -> bool:
        return any(run.first <= index <= run.last for run in self._runs.get(rank, ()))

    def evaluate(self, rank: int, indices: Sequence[int]) -> _Hypothetical | None:
        """Return the state after adding ``indices`` when it fits the budget, else ``None``."""

        previous = self._runs.get(rank, ())
        runs = previous
        for index in indices:
            runs = _extend_runs(runs, index)
        plan = self._plans[rank]
        fragments = tuple(_fragment(plan, run) for run in runs)
        item_bytes = tuple(self._item_size(fragment) for fragment in fragments)
        count = self._item_count - len(previous) + len(fragments)
        if count > self._fragment_limit:
            return None
        total = self._item_total - sum(self._item_bytes.get(rank, ())) + sum(item_bytes)
        total_bytes = _EMPTY_OUTPUT_BYTES + total + max(0, count - 1)
        if total_bytes > self._max_bytes:
            return None
        return _Hypothetical(
            runs=runs,
            item_bytes=item_bytes,
            fragments=fragments,
            total_bytes=total_bytes,
        )

    def try_add(self, rank: int, index: int) -> bool:
        return self.try_add_span(rank, index, index)

    def try_add_span(self, rank: int, first: int, last: int) -> bool:
        hypothetical = self.evaluate(rank, range(first, last + 1))
        if hypothetical is None:
            return False
        self._commit(rank, hypothetical)
        return True

    def _commit(self, rank: int, hypothetical: _Hypothetical) -> None:
        previous_count = len(self._runs.get(rank, ()))
        self._item_total += sum(hypothetical.item_bytes) - sum(self._item_bytes.get(rank, ()))
        self._item_count += len(hypothetical.fragments) - previous_count
        self._runs[rank] = hypothetical.runs
        self._item_bytes[rank] = hypothetical.item_bytes

    def adjacent_indices(self, rank: int) -> tuple[int, ...]:
        plan = self._plans[rank]
        runs = self._runs.get(rank, ())
        candidates: list[int] = []
        for run in runs:
            if run.last + 1 < len(plan.sentences):
                candidates.append(run.last + 1)
            if run.first > 0:
                candidates.append(run.first - 1)
        return tuple(dict.fromkeys(candidates))

    def _item_size(self, fragment: EvidenceFragment) -> int:
        key = (
            fragment.address.entry_id,
            fragment.address.entry_version_id,
            fragment.start,
            fragment.end,
        )
        size = self._item_cache.get(key)
        if size is None:
            size = _item_json_bytes(fragment)
            self._item_cache[key] = size
        return size


def _fragment(plan: _EntryPlan, run: _Run) -> EvidenceFragment:
    start = plan.sentences[run.first].start
    end = plan.sentences[run.last].end
    return EvidenceFragment(
        address=plan.candidate.address,
        start=start,
        end=end,
        text=plan.candidate.text[start:end],
    )


def _extend_runs(runs: Sequence[_Run], index: int) -> tuple[_Run, ...]:
    for run in runs:
        if run.first <= index <= run.last:
            return tuple(runs)
    extended: list[_Run] = []
    for run in runs:
        if run.last + 1 == index:
            run = replace(run, last=index)
        elif run.first - 1 == index:
            run = replace(run, first=index)
        extended.append(run)
    if not any(run.first <= index <= run.last for run in extended):
        extended.append(_Run(first=index, last=index))
    extended.sort(key=lambda run: (run.first, run.last))
    merged: list[_Run] = []
    for run in extended:
        if merged and run.first <= merged[-1].last + 1:
            merged[-1] = _Run(first=merged[-1].first, last=max(merged[-1].last, run.last))
        else:
            merged.append(run)
    return tuple(merged)


def _select_windows(selection: _Selection, plans: Sequence[_EntryPlan]) -> None:
    """Pick contiguous, budget-sized windows from the most promising entries.

    Scientific abstracts keep the reported result near their end: in the public SciFact split,
    more than half of the annotated evidence sentences sit in the last three sentences of the
    abstract. A contiguous window therefore beats scattering single sentences, and the window
    objective blends lexical relevance with a mild late-position prior so that a strongly
    matching sentence can still pull the window earlier.
    """

    ordered = sorted(plans, key=lambda plan: (-plan.entry_score, plan.rank))
    added = False
    for plan in ordered:
        if added and plan.entry_score <= 0.0:
            break
        window = _best_window(selection, plan)
        if window is not None:
            added = True


def _best_window(selection: _Selection, plan: _EntryPlan) -> tuple[int, int] | None:
    capacity = selection.max_bytes - selection.total_bytes
    if capacity <= 0:
        return None
    text = plan.candidate.text
    # One citation-only item has a fixed JSON shell; the varying parts of an item are the
    # literal content bytes and the digit widths of the two offsets.
    shell = _item_json_bytes(EvidenceFragment(address=plan.candidate.address, start=0, end=0, text=""))
    comma = 1 if selection.item_count else 0
    limit = capacity - comma
    if limit <= shell:
        return None
    prefix = _window_prefix(plan)
    windows: list[tuple[float, int, int]] = []
    for first in range(len(plan.sentences)):
        start = plan.sentences[first].start
        start_digits = len(str(start)) - 1
        for last in range(first, len(plan.sentences)):
            end = plan.sentences[last].end
            content = len(text[start:end].encode("utf-8"))
            estimated = shell + content + start_digits + len(str(end)) - 1
            if estimated > limit:
                break
            windows.append((_window_value(prefix, first, last), last, first))
    windows.sort(reverse=True)
    for _value, last, first in windows:
        if selection.try_add_span(plan.rank, first, last):
            return (first, last)
    return None


def _window_prefix(plan: _EntryPlan) -> tuple[tuple[float, ...], tuple[float, ...]]:
    count = len(plan.sentences)
    lexical = [0.0]
    positional = [0.0]
    for index in range(count):
        position = (index + 1) / count
        lexical.append(lexical[-1] + plan.scores[index])
        positional.append(positional[-1] + position**_POSITION_PRIOR_EXPONENT)
    return tuple(lexical), tuple(positional)


def _window_value(
    prefix: tuple[tuple[float, ...], tuple[float, ...]],
    first: int,
    last: int,
) -> float:
    lexical, positional = prefix
    return (lexical[last + 1] - lexical[first]) + _POSITION_PRIOR_WEIGHT * (
        positional[last + 1] - positional[first]
    )


def _fill_adjacent(selection: _Selection) -> None:
    """Spend any remaining budget on neighbouring sentences of already selected entries.

    Neighbouring text adds no citation overhead and can only widen the delivered intervals, so
    it is the cheapest way to keep evidence that shares few query terms.
    """

    changed = True
    while changed:
        changed = False
        for rank in selection.plan_ranks():
            for index in selection.adjacent_indices(rank):
                if selection.try_add(rank, index):
                    changed = True
                    break
            if changed:
                break
