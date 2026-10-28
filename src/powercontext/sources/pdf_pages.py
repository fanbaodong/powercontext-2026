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

"""Physical-page PDF Sources with page-level evidence and citation retrieval.

Each physical page of the original PDF becomes one captured Source so a retrieval
citation resolves back to the exact document revision and physical page that
carries the evidence text. Retrieval is built on public Memory searches: several
lexically focused queries cover the question, their hits are merged, and an
offline page-level reranker selects the most evidence-like distinct pages.
"""

from __future__ import annotations

import hashlib
import io
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, Field
from pypdf import PdfReader

from powercontext.artifacts import MemoryCitation
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_DEFINITION_VERSION = "1"
_PDF_SOURCE_ID_PREFIX = "pdf"
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}")


class PDFDocumentInput(BaseModel):
    """Complete offline PDF bytes plus the caller-owned document identity."""

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """One physical page of a PDF document, ready to be materialized as a Source."""

    document_id: str
    document_sha256: str
    page_number: int = Field(ge=1)
    text: str


class PDFPageSource(Source):
    """Captured Source for exactly one physical PDF page."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int = Field(ge=1)
    text: str


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Build a stable page identity from the document id, revision digest and page."""

    _validate_document_id(document_id)
    _validate_document_sha256(document_sha256)
    if page_number < 1:
        raise ValueError("page_number must be positive")
    readable = f"{_PDF_SOURCE_ID_PREFIX}:{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(readable) <= MAX_SOURCE_ID_LENGTH:
        return readable
    # Very long caller-owned document ids fall back to a fixed-size injective
    # encoding built from length-prefixed parts, keeping identity below the limit.
    canonical = (
        f"{_PDF_SOURCE_ID_PREFIX}\x00{len(document_id)}\x00{document_id}"
        f"\x00{document_sha256}\x00{page_number}"
    )
    return f"{_PDF_SOURCE_ID_PREFIX}:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Parse every physical page of one PDF document atomically.

    Empty, non-PDF, corrupt, password-protected or only partially readable input
    raises ``ValueError`` instead of returning a partial page list.
    """

    _validate_document_id(value.document_id)
    content = value.content
    if not content:
        raise ValueError("pdf document is empty")
    digest = hashlib.sha256(content).hexdigest()
    texts: list[str] = []
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("pdf document requires a password")
        pages = reader.pages
        page_count = len(pages)
        if page_count < 1:
            raise ValueError("pdf document has no pages")
        for page in pages:
            text = page.extract_text()
            texts.append(text if isinstance(text, str) else "")
    except ValueError:
        raise
    except Exception as error:  # pypdf raises several unrelated error types
        raise ValueError("pdf document cannot be parsed completely") from error
    return tuple(
        PDFPageInput(
            document_id=value.document_id,
            document_sha256=digest,
            page_number=index,
            text=text,
        )
        for index, text in enumerate(texts, start=1)
    )


class PDFPageSourceAdapter:
    """Resolve and read captured physical-page Sources."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        _validate_document_id(value.document_id)
        _validate_document_sha256(value.document_sha256)
        if value.page_number < 1:
            raise ValueError("page_number must be positive")
        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose one page as canonical text evidence with exact page metadata."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


def _validate_document_id(document_id: str) -> None:
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must not be empty or blank")


def _validate_document_sha256(document_sha256: str) -> None:
    if not isinstance(document_sha256, str) or _HEX_SHA256.fullmatch(document_sha256) is None:
        raise ValueError("document_sha256 must be a lowercase hexadecimal SHA-256 digest")


PDF_PAGE_SOURCE_ADAPTER = PDFPageSourceAdapter()
PDF_PAGE_TEXT_EVIDENCE_PROJECTION = PDFPageTextEvidenceProjection()
PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDF_PAGE_SOURCE_ADAPTER,
    projections=(PDF_PAGE_TEXT_EVIDENCE_PROJECTION,),
)


# ---------------------------------------------------------------------------
# Question analysis and lexical query planning
# ---------------------------------------------------------------------------

_TOKEN_PATTERN = re.compile(r"[a-z]+(?:'[a-z]+)?|\d+(?:[.,]\d+)*")
_DIGIT_PATTERN = re.compile(r"\d")
_MAX_SEARCH_QUERIES = 12
_MIN_SEARCH_HITS = 20
_RRF_K = 20.0
_BM25_K1 = 1.2
_BM25_B = 0.75

_FUNCTION_WORDS = frozenset(
    """
    a an the and or nor but if then than that this these those there here
    of to in on at by for from with without into onto over under about above below
    between among within during before after as is are was were be been being am do
    does did done doing have has had having can could should would will shall may
    might must i me my we our us you your he she it its they them their what which
    who whom whose how why when where whether all any both each few more most other
    some such no not only own same so too very just also fy
    """.split()
)

_META_WORDS = frozenset(
    """
    question questions answer answers replied respond response responses reply
    calculate calculated calculation compute computed determine state stated
    specify give gives provide provides tell explain describe identify list show
    shown using use used according based rely relying assume assuming assumption
    perspective please kindly following details detail define defined
    """.split()
)

# Common financial-report vocabulary that carries little discriminating signal
# on its own. It still contributes to coverage and reranking, just less.
_GENERIC_TERMS = frozenset(
    """
    company companies fiscal fy year years report reports reported reporting
    financial statement statements total net income operating cash flow flows
    business businesses product products market markets million millions billion
    billions period end ended december 2020 2021 2022 2023 2024
    """.split()
)

# Question modifiers that rarely appear in the evidence passage itself.
_LOW_INFORMATION_WORDS = frozenset(
    """
    materially important ongoing directly explicitly outlined primarily
    approximately currently recently historically significantly substantially
    generally typically related regarding various several certain overall
    continue continued include includes including based accordance respect
    ability effect effects impact impacts matter matters material
    """.split()
)


@dataclass(frozen=True)
class _Concept:
    """One generic finance-report concept and the terms that express it."""

    name: str
    triggers: frozenset[str]
    expansions: tuple[str, ...]


_CONCEPTS: tuple[_Concept, ...] = (
    _Concept(
        "geography",
        frozenset({"geogra", "region", "countr", "territor"}),
        ("segments", "countries", "regions", "markets", "international", "businesses", "divisions"),
    ),
    _Concept(
        "legal",
        frozenset({"legal", "litig", "battle", "lawsuit", "regulat"}),
        ("litigation", "proceedings", "claims", "regulatory", "investigations", "disputes"),
    ),
    _Concept(
        "restructuring",
        frozenset({"restru"}),
        ("restructuring", "impairment", "charges", "productivity", "severance"),
    ),
    _Concept(
        "capex",
        frozenset({"capex", "capital"}),
        ("capital", "expenditures", "spending", "property", "plant", "equipment"),
    ),
    _Concept(
        "ebitda",
        frozenset({"ebitda", "unadjust"}),
        ("operating", "income", "profit", "depreciation", "amortization"),
    ),
    _Concept(
        "margin",
        frozenset({"margin", "profitab"}),
        ("revenue", "sales", "profit", "income", "operating"),
    ),
    _Concept(
        "revenue",
        frozenset({"revenu", "sale"}),
        ("revenue", "sales", "net"),
    ),
    _Concept(
        "employee",
        frozenset({"employ"}),
        ("employees", "headcount", "workforce", "personnel"),
    ),
    _Concept(
        "dividend",
        frozenset({"divide", "payout"}),
        ("dividend", "dividends", "payout", "shareholders"),
    ),
    _Concept(
        "debt",
        frozenset({"debt", "borrow"}),
        ("debt", "borrowings", "obligations", "notes", "interest"),
    ),
    _Concept(
        "liquidity",
        frozenset({"liquid"}),
        ("liquidity", "cash", "flows", "capital"),
    ),
    _Concept(
        "shares",
        frozenset({"share", "stock"}),
        ("shares", "stock", "repurchase", "buyback", "outstanding"),
    ),
    _Concept(
        "segment",
        frozenset({"segment", "division"}),
        ("segment", "segments", "business", "sales", "operating", "income"),
    ),
    _Concept(
        "ratio",
        frozenset({"ratio"}),
        ("current", "assets", "liabilities", "cash", "equivalents", "investments"),
    ),
    _Concept(
        "concentration",
        frozenset({"concentr"}),
        ("customer", "customers", "accounted", "revenue", "sales"),
    ),
    _Concept(
        "inventory",
        frozenset({"inventor", "turnover"}),
        ("inventories", "inventory", "cost", "sales", "goods"),
    ),
)

_REGION_PHRASES = (
    "north america",
    "latin america",
    "south america",
    "europe",
    "africa",
    "middle east",
    "south asia",
    "asia pacific",
    "australia",
    "new zealand",
    "china",
    "canada",
    "mexico",
    "united kingdom",
    "russia",
    "brazil",
    "india",
    "japan",
)


@dataclass(frozen=True)
class _Token:
    surface: str
    stem: str


@dataclass(frozen=True)
class _PlannedQuery:
    text: str
    weight: float


@dataclass(frozen=True)
class _QuestionProfile:
    stems: tuple[str, ...]
    weights: Mapping[str, float]
    concepts: tuple[_Concept, ...]
    expansion_stems: tuple[str, ...]
    phrases: tuple[tuple[str, ...], ...]


@dataclass
class _Candidate:
    memory_ref: Any
    entry_id: str
    entry_version_id: str
    text: str
    reciprocal_rank: float = 0.0

    @property
    def citation(self) -> MemoryCitation:
        return MemoryCitation(
            memory_ref=self.memory_ref,
            entry_id=self.entry_id,
            entry_version_id=self.entry_version_id,
        )

    @property
    def identity(self) -> tuple[str, str, int, str, str]:
        return (
            self.memory_ref.family,
            self.memory_ref.artifact_id,
            self.memory_ref.revision,
            self.entry_id,
            self.entry_version_id,
        )


@dataclass(frozen=True)
class _PageProfile:
    text: str
    stems: tuple[str, ...]
    counts: Counter[str]
    length: int


@dataclass(frozen=True)
class _Features:
    """Unnormalized reranking evidence collected for one candidate page."""

    question_score: float
    expansion_score: float
    coverage: float
    concept_coverage: float
    phrase_score: float
    prior: float


def _tokens(value: str) -> tuple[_Token, ...]:
    normalized = unicodedata.normalize("NFC", value).casefold()
    tokens: list[_Token] = []
    for match in _TOKEN_PATTERN.finditer(normalized):
        surface = match.group()
        if _DIGIT_PATTERN.search(surface):
            surface = surface.replace(",", "")
        tokens.append(_Token(surface=surface, stem=_stem_key(surface)))
    return tuple(tokens)


def _stem_key(token: str) -> str:
    """Return a compact prefix key that treats common inflections as equal."""

    if _DIGIT_PATTERN.search(token):
        return token
    stem = token
    if len(stem) <= 3:
        return stem
    if stem.endswith("ies") and len(stem) > 4:
        stem = f"{stem[:-3]}y"
    elif stem.endswith(("sses", "shes", "ches", "xes", "zes")):
        stem = stem[:-2]
    elif stem.endswith("s") and not stem.endswith(("ss", "us", "is")):
        stem = stem[:-1]
    if stem.endswith("ing") and len(stem) > 5:
        stem = stem[:-3]
    elif stem.endswith("ed") and len(stem) > 4:
        stem = stem[:-2]
    elif stem.endswith("ly") and len(stem) > 5:
        stem = stem[:-2]
    if stem.endswith("e") and len(stem) > 4:
        stem = stem[:-1]
    return stem[:6] if len(stem) > 6 else stem


def _is_content(token: _Token) -> bool:
    surface = token.surface
    if surface in _FUNCTION_WORDS or surface in _META_WORDS:
        return False
    return len(surface) >= 2


def _content_tokens(question: str) -> tuple[_Token, ...]:
    return tuple(token for token in _tokens(question) if _is_content(token))


def _dedupe_tokens(tokens: Sequence[_Token]) -> tuple[_Token, ...]:
    seen: set[str] = set()
    unique: list[_Token] = []
    for token in tokens:
        if token.surface in seen:
            continue
        seen.add(token.surface)
        unique.append(token)
    return tuple(unique)


def _term_weight(token: _Token) -> float:
    surface = token.surface
    if surface in _LOW_INFORMATION_WORDS:
        return 0.3
    if surface in _GENERIC_TERMS:
        return 0.5
    weight = 1.0
    if _DIGIT_PATTERN.search(surface):
        weight = 0.6
    if any(_concept_matches(concept, token.stem) for concept in _CONCEPTS):
        weight = max(weight, 1.6)
    return weight


def _concept_matches(concept: _Concept, stem: str) -> bool:
    return any(_stem_key(trigger) == stem for trigger in concept.triggers)


def _term_priority(token: _Token) -> float:
    surface = token.surface
    priority = _term_weight(token)
    if len(surface) >= 10:
        priority += 1.0
    elif len(surface) >= 8:
        priority += 0.6
    if surface in _LOW_INFORMATION_WORDS:
        priority -= 2.0
    if _DIGIT_PATTERN.search(surface):
        priority -= 0.2
    return priority


def _triggered_concepts(content: Sequence[_Token]) -> tuple[_Concept, ...]:
    stems = {token.stem for token in content}
    selected: list[_Concept] = []
    claimed: set[str] = set()
    for concept in _CONCEPTS:
        if not any(_concept_matches(concept, stem) for stem in stems):
            continue
        overlap = claimed.intersection(concept.triggers)
        if overlap:
            continue
        claimed.update(concept.triggers)
        selected.append(concept)
        if len(selected) == 2:
            break
    return tuple(selected)


def _content_phrases(content: Sequence[_Token]) -> tuple[tuple[_Token, ...], ...]:
    phrases: list[tuple[_Token, ...]] = []
    for size in (2, 3):
        for index in range(len(content) - size + 1):
            phrase = tuple(content[index : index + size])
            if all(
                token.surface in _GENERIC_TERMS or token.surface in _LOW_INFORMATION_WORDS
                for token in phrase
            ):
                continue
            phrases.append(phrase)
    return tuple(phrases)


def _phrase_priority(phrase: Sequence[_Token]) -> float:
    return sum(_term_priority(token) for token in phrase) + 0.4 * (len(phrase) - 2)


def _analyze_question(question: str) -> _QuestionProfile:
    content = _content_tokens(question)
    tokens = _dedupe_tokens(content)
    weights: dict[str, float] = {}
    stems: list[str] = []
    for token in tokens:
        if token.stem not in weights:
            stems.append(token.stem)
        weights[token.stem] = max(weights.get(token.stem, 0.0), _term_weight(token))
    concepts = _triggered_concepts(content)
    expansion_stems = tuple(
        dict.fromkeys(_stem_key(term) for concept in concepts for term in concept.expansions)
    )
    ranked = sorted(_content_phrases(content), key=_phrase_priority, reverse=True)
    phrases: list[tuple[str, ...]] = []
    for phrase in ranked:
        if len(phrases) >= 4:
            break
        stems_phrase = tuple(dict.fromkeys(token.stem for token in phrase))
        if stems_phrase in phrases:
            continue
        phrases.append(stems_phrase)
    return _QuestionProfile(
        stems=tuple(stems),
        weights=weights,
        concepts=concepts,
        expansion_stems=expansion_stems,
        phrases=tuple(phrases),
    )


def _build_query_plan(question: str) -> tuple[_PlannedQuery, ...]:
    normalized = " ".join(question.split())
    profile = _analyze_question(normalized)
    if not profile.stems:
        return ()
    content = _dedupe_tokens(_content_tokens(normalized))
    plan: list[_PlannedQuery] = []

    keyword_query = " ".join(token.surface for token in content)
    if len(content) >= 2:
        plan.append(_PlannedQuery(keyword_query, 1.0))

    for index, concept in enumerate(profile.concepts):
        plan.append(_PlannedQuery(" ".join(concept.expansions), 1.3 - 0.1 * index))

    informative = [
        token
        for token in content
        if token.surface not in _LOW_INFORMATION_WORDS
        and token.surface not in _FUNCTION_WORDS
        and token.surface not in _META_WORDS
    ]
    for token in sorted(informative, key=_term_priority, reverse=True)[:5]:
        plan.append(_PlannedQuery(token.surface, 0.5))

    # Keep the concrete surfaces of the strongest content phrases for search.
    surface_phrases = sorted(
        _content_phrases(content),
        key=_phrase_priority,
        reverse=True,
    )[:4]
    for phrase in surface_phrases:
        plan.append(_PlannedQuery(" ".join(token.surface for token in phrase), 0.7))

    seen: set[str] = set()
    unique: list[_PlannedQuery] = []
    for item in plan:
        key = item.text.casefold().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) == _MAX_SEARCH_QUERIES:
            break
    if not unique:
        unique.append(_PlannedQuery(normalized, 1.0))
    return tuple(unique)


def _page_text(entry_text: str) -> str:
    _, separator, page_text = entry_text.partition("\n")
    return page_text if separator else entry_text


def _profile(text: str) -> _PageProfile:
    tokens = _tokens(text)
    stems = tuple(token.stem for token in tokens)
    return _PageProfile(
        text=text,
        stems=stems,
        counts=Counter(stems),
        length=max(1, len(stems)),
    )


def _bm25(
    terms: Sequence[str],
    weights: Mapping[str, float],
    profile: _PageProfile,
    *,
    average_length: float,
    document_frequencies: Mapping[str, int],
    document_count: int,
) -> float:
    score = 0.0
    for term in terms:
        frequency = profile.counts.get(term, 0)
        if not frequency:
            continue
        frequency_documents = document_frequencies.get(term, 0)
        inverse_document_frequency = math.log(
            1.0 + (document_count - frequency_documents + 0.5) / (frequency_documents + 0.5)
        )
        normalization = _BM25_K1 * (1.0 - _BM25_B + _BM25_B * profile.length / average_length)
        score += (
            weights.get(term, 1.0)
            * inverse_document_frequency
            * frequency
            * (_BM25_K1 + 1.0)
            / (frequency + normalization)
        )
    return score


def _contains(sequence: Sequence[str], phrase: Sequence[str]) -> bool:
    if not phrase:
        return False
    for index in range(len(sequence) - len(phrase) + 1):
        if tuple(sequence[index : index + len(phrase)]) == tuple(phrase):
            return True
    return False


def _compact(text: str) -> str:
    return " ".join(text.casefold().split())


def _structural_prior(text: str, concepts: Sequence[_Concept]) -> float:
    """Reward canonical financial-report sections for the question's concept."""

    names = {concept.name for concept in concepts}
    compact = _compact(text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    heading = " ".join(lines[:8]).casefold()
    # pypdf sometimes splits words across text runs ("Cash Flow s", "T able"),
    # so compare statement titles without any separators.
    squashed = re.sub(r"[^a-z0-9]+", "", heading)
    prior = 0.0
    is_income = any(
        title in squashed
        for title in (
            "consolidatedstatementofincome",
            "consolidatedstatementsofincome",
            "consolidatedstatementofoperations",
            "consolidatedstatementsofoperations",
            "consolidatedstatementofearnings",
            "consolidatedstatementsofearnings",
        )
    )
    is_balance = "balancesheet" in squashed
    is_cash = "statementofcashflows" in squashed or "statementsofcashflows" in squashed
    if is_income and names & {"income", "margin", "revenue", "ebitda"}:
        prior += 0.9
    if is_balance and names & {"liquidity", "debt", "ratio"}:
        prior += 1.0
    if is_cash and names & {"capex", "ebitda", "liquidity", "dividend"}:
        prior += 0.9
    if "segment" in names and (
        "business segment" in compact or ("segments" in compact and "sales" in compact)
    ):
        prior += 0.8
    if "concentration" in names and "customer" in compact and (
        "accounted for" in compact or "% of" in compact or "concentration" in compact
    ):
        prior += 0.9
    if "inventory" in names:
        if is_balance and "inventories" in compact:
            prior += 1.0
        if "cost of sales" in compact or "cost of goods sold" in compact:
            prior += 0.5
        if "inventory turnover" in compact:
            prior += 0.5
    if "margin" in names and ("gross margin" in compact or "gross profit" in compact):
        prior += 0.5
    if "margin" in names and "analysis of consolidated earnings" in compact:
        prior += 0.6
    if is_income and "capex" in names:
        prior += 0.4
    if "legal" in names and "item 3. legal proceedings" in compact and "litigation" in compact:
        prior += 0.8
    if "geography" in names and "reportable segments" in compact:
        prior += 0.5
    if "geography" in names and sum(1 for region in _REGION_PHRASES if region in compact) >= 4:
        prior += 0.5
    if "restructuring" in names and "restructuring and impairment charges" in compact:
        prior += 0.3
    return prior


def _features(
    candidates: Sequence[_Candidate],
    question: str,
) -> tuple[tuple[_Features, ...], tuple[tuple[str, ...], ...]]:
    profile = _analyze_question(question)
    profiles = [_profile(_page_text(candidate.text)) for candidate in candidates]
    document_count = max(1, len(profiles))
    average_length = sum(page.length for page in profiles) / document_count

    terms = tuple(dict.fromkeys((*profile.stems, *profile.expansion_stems)))
    document_frequencies = {
        term: sum(1 for page in profiles if term in page.counts) for term in terms
    }
    expansion_weights = {stem: 0.45 for stem in profile.expansion_stems}
    all_weights = dict(profile.weights)
    all_weights.update(expansion_weights)
    total_weight = sum(all_weights.values()) or 1.0
    expansion_total = len(profile.expansion_stems)
    phrase_weights = {
        phrase: sum(profile.weights.get(stem, 0.5) for stem in phrase) for phrase in profile.phrases
    }

    features: list[_Features] = []
    for page in profiles:
        matched_weight = sum(weight for stem, weight in all_weights.items() if page.counts.get(stem))
        concept_matches = sum(
            1 for stem in profile.expansion_stems if page.counts.get(stem)
        )
        phrase_score = sum(
            phrase_weights[phrase]
            for phrase in profile.phrases
            if _contains(page.stems, phrase)
        )
        features.append(
            _Features(
                question_score=_bm25(
                    profile.stems,
                    profile.weights,
                    page,
                    average_length=average_length,
                    document_frequencies=document_frequencies,
                    document_count=document_count,
                ),
                expansion_score=_bm25(
                    profile.expansion_stems,
                    expansion_weights,
                    page,
                    average_length=average_length,
                    document_frequencies=document_frequencies,
                    document_count=document_count,
                ),
                coverage=matched_weight / total_weight,
                concept_coverage=(concept_matches / expansion_total) if expansion_total else 0.0,
                phrase_score=phrase_score,
                prior=_structural_prior(page.text, profile.concepts),
            )
        )
    return tuple(features), tuple(profile.phrases)


def _normalize(values: Sequence[float]) -> tuple[float, ...]:
    if not values:
        return ()
    low = min(values)
    high = max(values)
    if high <= low:
        return tuple(0.0 for _ in values)
    return tuple((value - low) / (high - low) for value in values)


def _rank_candidates(
    candidates: Sequence[_Candidate],
    question: str,
) -> list[_Candidate]:
    if not candidates:
        return []
    features, _phrases = _features(candidates, question)
    question_scores = _normalize([feature.question_score for feature in features])
    expansion_scores = _normalize([feature.expansion_score for feature in features])
    phrase_scores = _normalize([feature.phrase_score for feature in features])
    reciprocal_ranks = _normalize([candidate.reciprocal_rank for candidate in candidates])

    scored: list[tuple[float, _Candidate]] = []
    for index, candidate in enumerate(candidates):
        feature = features[index]
        score = (
            0.9 * question_scores[index]
            + 0.4 * expansion_scores[index]
            + 0.5 * feature.coverage
            + 0.35 * feature.concept_coverage
            + 0.3 * phrase_scores[index]
            + 0.5 * reciprocal_ranks[index]
            + feature.prior
        )
        scored.append((score, candidate))
    scored.sort(key=lambda item: (-item[0], item[1].entry_id, item[1].entry_version_id))
    return [candidate for _score, candidate in scored]


async def search_pdf_pages(
    memory: Any,
    question: str,
    limit: int = 10,
) -> tuple[MemoryCitation, ...]:
    """Return up to ``limit`` distinct evidence-page citations for one question."""

    if limit < 1 or not question or not question.strip():
        return ()
    plan = _build_query_plan(question)
    if not plan:
        return ()
    from powercontext.builtin.runtime.models import GetMemoryEntryRequest, SearchMemoryRequest

    candidates: dict[tuple[str, str, int, str, str], _Candidate] = {}
    search_limit = max(limit, _MIN_SEARCH_HITS)
    for planned in plan:
        page = await memory.search(SearchMemoryRequest(query=planned.text, mode="fts", limit=search_limit))
        for rank, hit in enumerate(page.hits, start=1):
            candidate = _Candidate(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
                text=hit.text,
            )
            existing = candidates.get(candidate.identity)
            contribution = planned.weight / (_RRF_K + rank)
            if existing is None:
                candidate.reciprocal_rank = contribution
                candidates[candidate.identity] = candidate
            else:
                existing.reciprocal_rank += contribution

    # Score every captured page, not only the pages the lexical index surfaced:
    # table-heavy annual reports often place the evidence on a statement page
    # whose wording differs from the question, and a top-N index cut loses it.
    records = await memory.list()
    for record in records.entries:
        sources = record.entry.sources
        if len(sources) != 1:
            continue
        reference = sources[0]
        if reference.source_type != PDF_PAGE_SOURCE_NAME:
            continue
        corpus_candidate = _Candidate(
            memory_ref=record.memory_ref,
            entry_id=record.entry.entry_id,
            entry_version_id=record.entry.entry_version_id,
            text=record.entry.text,
        )
        candidates.setdefault(corpus_candidate.identity, corpus_candidate)
    if not candidates:
        return ()

    ranked = _rank_candidates(tuple(candidates.values()), question)
    chosen: list[MemoryCitation] = []
    seen_pages: set[str] = set()
    attempts = 0
    maximum_attempts = limit * 4 + 20
    for candidate in ranked:
        if len(chosen) >= limit or attempts >= maximum_attempts:
            break
        attempts += 1
        entry = (await memory.get(GetMemoryEntryRequest(citation=candidate.citation))).entry
        if len(entry.sources) != 1:
            continue
        reference = entry.sources[0]
        if reference.source_type != PDF_PAGE_SOURCE_NAME or reference.source_id in seen_pages:
            continue
        seen_pages.add(reference.source_id)
        chosen.append(candidate.citation)
    return tuple(chosen)


__all__ = [
    "PDF_PAGE_DEFINITION_VERSION",
    "PDF_PAGE_SOURCE_ADAPTER",
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDF_PAGE_TEXT_EVIDENCE_PROJECTION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
