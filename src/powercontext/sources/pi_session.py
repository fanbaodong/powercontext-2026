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

"""Pi v3 session branches as immutable per-event Sources with resumable capture.

A Pi session file is a JSONL tree: every record names its parent, and a selected
leaf plus its ancestors form one visible history path.  This module parses that
path into complete original events (without executing recorded tool calls),
materializes each visible event as one captured Source, and incrementally
commits the events that were not imported before.  The checkpoint keeps the
identity and a canonical digest of every imported record so that a restart can
deduplicate without re-reading history and so that a rewritten record is
rejected instead of overwriting the persisted evidence.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
)
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PI_EVENT_SOURCE_NAME = "pi-event"
PI_EVENT_DEFINITION_NAME = PI_EVENT_SOURCE_NAME
PI_EVENT_DEFINITION_VERSION = "1"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_CONNECTOR_VERSION = "1"

_PI_EVENT_ID_PREFIX = "pi"
_PI_EVENT_HASH_PREFIX = "pi-hash"
_CHECKPOINT_VERSION = 1
_PI_SESSION_VERSION = 3


class PiSessionInput(BaseModel):
    """Complete offline Pi session bytes plus the explicitly selected leaf."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event on the selected branch, ready to become a Source."""

    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """Captured Source for exactly one visible Pi session event."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class _PiSessionGraph:
    """Validated session tree with file order preserved."""

    session_id: str
    event_ids: tuple[str, ...]
    records: dict[str, dict[str, JsonValue]]
    parent_ids: dict[str, str | None]
    leaf_id: str | None


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Build the stable event identity from the session id and event id.

    The readable form quotes both identifiers so the encoding is injective while
    staying inside the Source id limit; oversized identities fall back to a
    digest of the same quoted pair so they remain bounded and deterministic.
    """

    _validate_identity_part("session_id", session_id)
    _validate_identity_part("event_id", event_id)
    encoded = (
        f"{quote(session_id, safe='', errors='surrogatepass')}:"
        f"{quote(event_id, safe='', errors='surrogatepass')}"
    )
    source_id = f"{_PI_EVENT_ID_PREFIX}:{encoded}"
    if len(source_id) <= MAX_SOURCE_ID_LENGTH:
        return source_id
    digest = hashlib.sha256(encoded.encode("utf-8", "surrogatepass")).hexdigest()
    return f"{_PI_EVENT_HASH_PREFIX}:{digest}"


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Parse the selected branch into visible events ordered from root to leaf.

    The whole archive is validated atomically.  Any encoding, JSON, header,
    duplicate id, missing parent, cycle, or missing leaf error raises
    ``ValueError`` and no partial event is returned.  Control nodes keep their
    place in the tree but do not produce text evidence.
    """

    graph = _parse_session_graph(value)
    return _branch_events(graph)


def _parse_session_graph(value: PiSessionInput, /) -> _PiSessionGraph:
    records = _read_json_lines(value.content)
    if not records:
        raise ValueError("pi session must contain a session header")

    header = records[0]
    if header.get("type") != "session":
        raise ValueError("pi session first record must have type 'session'")
    version = header.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != _PI_SESSION_VERSION:
        raise ValueError(f"pi session version must be {_PI_SESSION_VERSION}")
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("pi session id must be a non-empty string")

    event_ids: list[str] = []
    parsed: dict[str, dict[str, JsonValue]] = {}
    parent_ids: dict[str, str | None] = {}
    for record in records[1:]:
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("pi session records must have a non-empty string id")
        if event_id in parsed:
            raise ValueError(f"duplicate pi session record id {event_id!r}")
        if "parentId" not in record:
            raise ValueError(f"pi session record {event_id!r} is missing parentId")
        parent_id = record["parentId"]
        if parent_id is not None:
            if not isinstance(parent_id, str) or not parent_id:
                raise ValueError(f"pi session record {event_id!r} has an invalid parentId")
            if parent_id not in parsed:
                raise ValueError(
                    f"pi session record {event_id!r} references unknown parent {parent_id!r}"
                )
        event_ids.append(event_id)
        parsed[event_id] = record
        parent_ids[event_id] = parent_id

    if value.leaf_id is not None:
        if value.leaf_id not in parsed:
            raise ValueError(f"pi session leaf {value.leaf_id!r} is not present in the archive")
        leaf_id = value.leaf_id
    else:
        leaf_id = event_ids[-1] if event_ids else None
    return _PiSessionGraph(
        session_id=session_id,
        event_ids=tuple(event_ids),
        records=parsed,
        parent_ids=parent_ids,
        leaf_id=leaf_id,
    )


def _read_json_lines(content: bytes) -> list[dict[str, JsonValue]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("pi session content must be valid UTF-8") from error

    records: list[dict[str, JsonValue]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("pi session contains malformed JSON") from error
        if not isinstance(record, dict):
            raise ValueError("pi session records must be JSON objects")
        records.append(record)
    return records


def _branch_events(graph: _PiSessionGraph) -> tuple[PiEventInput, ...]:
    chain: list[str] = []
    visited: set[str] = set()
    cursor = graph.leaf_id
    while cursor is not None:
        if cursor in visited:
            raise ValueError("pi session graph contains a cycle")
        visited.add(cursor)
        chain.append(cursor)
        cursor = graph.parent_ids[cursor]
    chain.reverse()

    events: list[PiEventInput] = []
    for event_id in chain:
        event = _event_for_record(
            graph.session_id,
            event_id,
            graph.records[event_id],
            graph.parent_ids[event_id],
        )
        if event is not None:
            events.append(event)
    return tuple(events)


def _event_for_record(
    session_id: str,
    event_id: str,
    record: dict[str, JsonValue],
    parent_id: str | None,
) -> PiEventInput | None:
    event_type = record.get("type")
    if not isinstance(event_type, str):
        return None

    role: str
    body: str | None
    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        message_role = message.get("role")
        if not isinstance(message_role, str) or not message_role:
            return None
        role = message_role
        body = _message_body(role, message)
    elif event_type in {"compaction", "branch_summary"}:
        role = event_type
        summary = record.get("summary")
        body = summary if isinstance(summary, str) else None
    elif event_type == "custom_message":
        role = "custom"
        body = _text_content(record.get("content"))
    else:
        # Control records preserve the graph but never become text evidence.
        return None

    if body is None or not body.strip():
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=event_id,
        parent_id=parent_id,
        event_type=event_type,
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _message_body(role: str, message: dict[str, JsonValue]) -> str | None:
    if role == "toolResult":
        text = _text_content(message.get("content")) or ""
        tool_name = message.get("toolName")
        tool_call_id = message.get("toolCallId")
        return (
            f"[tool_result {tool_name if isinstance(tool_name, str) else ''} "
            f"{tool_call_id if isinstance(tool_call_id, str) else ''}] {text}"
        )
    if role == "bashExecution":
        command = message.get("command")
        if not isinstance(command, str) or not command:
            return None
        output = message.get("output")
        if isinstance(output, str) and output:
            return f"$ {command}\n{output}"
        return f"$ {command}"
    return _content_body(message.get("content"))


def _content_body(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        elif block_type == "toolCall":
            parts.append(_tool_call_segment(block))
    if not parts:
        return None
    return "\n".join(parts)


def _text_content(content: object) -> str | None:
    """Keep visible strings and text blocks, ignoring thinking and images."""

    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts = [
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
        and block["text"]
    ]
    if not parts:
        return None
    return "\n".join(parts)


def _tool_call_segment(block: dict[str, Any]) -> str:
    name = block.get("name")
    call_id = block.get("id")
    arguments = block.get("arguments")
    if arguments is None:
        arguments = {}
    return (
        f"[tool_call {name if isinstance(name, str) else ''} "
        f"{call_id if isinstance(call_id, str) else ''}] {_compact_json(arguments)}"
    )


def _compact_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError("pi tool call arguments must be JSON serializable") from error


def _record_fingerprint(record: dict[str, JsonValue]) -> str:
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8", "surrogatepass")).hexdigest()


def _validate_identity_part(field: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")


class PiEventSourceAdapter:
    """Resolve and read captured Pi event Sources."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Expose the complete event text with its exact identity metadata."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_ADAPTER = PiEventSourceAdapter()
PI_EVENT_TEXT_EVIDENCE_PROJECTION = PiEventTextEvidenceProjection()
PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PI_EVENT_SOURCE_ADAPTER,
    version=PI_EVENT_DEFINITION_VERSION,
    projections=(PI_EVENT_TEXT_EVIDENCE_PROJECTION,),
)


class PiSessionConnector:
    """Import the visible events of one Pi session branch incrementally."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SESSION_CONNECTOR_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    @property
    def path(self) -> Path:
        return self._path

    @property
    def leaf_id(self) -> str | None:
        return self._leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        graph = _parse_session_graph(
            PiSessionInput(content=self._path.read_bytes(), leaf_id=self._leaf_id)
        )
        imported = _load_checkpoint(session.checkpoint, graph.session_id)
        self._reject_rewritten_records(graph, imported)

        pending = tuple(
            event
            for event in _branch_events(graph)
            if event.event_id not in imported
        )
        for event in pending:
            await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)

        events = dict(imported)
        for event in pending:
            events[event.event_id] = {
                "name": pi_event_source_id(event.session_id, event.event_id),
                "fingerprint": _record_fingerprint(event.record),
            }
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={
                "version": _CHECKPOINT_VERSION,
                "session_id": graph.session_id,
                "events": events,
            },
        )

    @staticmethod
    def _reject_rewritten_records(
        graph: _PiSessionGraph,
        imported: dict[str, dict[str, str]],
    ) -> None:
        for event_id, entry in imported.items():
            record = graph.records.get(event_id)
            if record is None:
                # The record is no longer present in the archive; imported
                # evidence is retained and cannot be re-verified.
                continue
            if entry["name"] != pi_event_source_id(graph.session_id, event_id):
                raise ValueError(f"pi session event {event_id!r} changed its identity after import")
            if entry["fingerprint"] != _record_fingerprint(record):
                raise ValueError(f"pi session event {event_id!r} was rewritten after import")


def _load_checkpoint(
    checkpoint: JsonValue | None,
    session_id: str,
) -> dict[str, dict[str, str]]:
    if checkpoint is None:
        return {}
    if not isinstance(checkpoint, dict):
        raise ValueError("pi session checkpoint must be an object")
    if checkpoint.get("version") != _CHECKPOINT_VERSION:
        raise ValueError("pi session checkpoint version is not supported")
    if checkpoint.get("session_id") != session_id:
        raise ValueError("pi session identity changed after imported evidence was captured")
    events = checkpoint.get("events")
    if not isinstance(events, dict):
        raise ValueError("pi session checkpoint must contain an events object")

    imported: dict[str, dict[str, str]] = {}
    for event_id, value in events.items():
        if not isinstance(event_id, str) or not event_id or not isinstance(value, dict):
            raise ValueError("pi session checkpoint contains an invalid event entry")
        name = value.get("name")
        fingerprint = value.get("fingerprint")
        if not isinstance(name, str) or not name or not isinstance(fingerprint, str) or not fingerprint:
            raise ValueError("pi session checkpoint contains an invalid event entry")
        imported[event_id] = {"name": name, "fingerprint": fingerprint}
    return imported


__all__ = [
    "PI_EVENT_DEFINITION_NAME",
    "PI_EVENT_DEFINITION_VERSION",
    "PI_EVENT_SOURCE_ADAPTER",
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_EVENT_TEXT_EVIDENCE_PROJECTION",
    "PI_SESSION_CONNECTOR_NAME",
    "PI_SESSION_CONNECTOR_VERSION",
    "PiEventInput",
    "PiEventSource",
    "PiEventSourceAdapter",
    "PiEventTextEvidenceProjection",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
