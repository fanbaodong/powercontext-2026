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

"""Self-describing chunked Context Packs and prefix-reusing deltas.

A receiver only needs the previous full pack and the next delta to rebuild the
current prepared context byte for byte; the encoding is canonical JSON with
Base64 chunks and SHA-256 digests so independent implementations interoperate.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
_DIGEST_PREFIX = "sha256:"
_DEFAULT_CHUNK_BYTES = 512
_PACK_FIELDS = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})


class ContextPackError(ValueError):
    """Raised when a pack, delta or chunk boundary violates the protocol."""


@dataclass(frozen=True, slots=True)
class ContextPack:
    """One complete context represented as ordered UTF-8 chunk bytes."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        """Build the canonical pack for already chunked content."""

        normalized = _validate_items(items)
        digest = _pack_digest(normalized)
        payload = _pack_payload(normalized, digest, 0)
        byte_count = len(payload)
        while True:
            payload = _pack_payload(normalized, digest, byte_count)
            new_count = len(payload)
            if new_count == byte_count:
                break
            byte_count = new_count
        return cls(items=normalized, digest=digest, byte_count=byte_count)

    @classmethod
    def from_content(cls, content: str | bytes, *, chunk_bytes: int = _DEFAULT_CHUNK_BYTES) -> ContextPack:
        """Deterministically chunk valid UTF-8 content into bounded byte chunks."""

        if not isinstance(chunk_bytes, int) or isinstance(chunk_bytes, bool) or chunk_bytes < 1:
            raise ValueError("chunk_bytes must be a positive integer")
        if isinstance(content, str):
            raw = content.encode("utf-8")
        elif isinstance(content, bytes):
            raw = content
        else:
            raise TypeError("content must be str or bytes")
        return cls.build(_chunk_utf8(raw, chunk_bytes))

    def to_bytes(self) -> bytes:
        """Serialize the full pack as canonical JSON."""

        return _pack_payload(self.items, self.digest, self.byte_count)

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        """Decode and fully validate one serialized pack."""

        payload = _load_json_object(data, _PACK_FIELDS)
        if payload["schema"] != PACK_SCHEMA:
            raise ContextPackError("unsupported context pack schema")
        items = _decode_items(payload["items"])
        digest = payload["digest"]
        byte_count = payload["byte_count"]
        if not isinstance(digest, str) or digest != _pack_digest(items):
            raise ContextPackError("context pack digest does not match its items")
        if not isinstance(byte_count, int) or isinstance(byte_count, bool):
            raise ContextPackError("context pack byte_count must be an integer")
        pack = cls(items=items, digest=digest, byte_count=byte_count)
        if byte_count != len(pack.to_bytes()):
            raise ContextPackError("context pack byte_count does not match its encoding")
        return pack


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """A target pack expressed as a retained base prefix plus appended chunks."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        """Keep a common prefix of the base pack and append the target suffix."""

        limit = min(len(base.items), len(target.items))
        prefix = 0
        while prefix < limit and base.items[prefix] == target.items[prefix]:
            prefix += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix,
            appended=target.items[prefix:],
        )

    @property
    def byte_count(self) -> int:
        """Wire size of this delta; not serialized as a field."""

        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize the delta as canonical JSON."""

        return _canonical_json(
            {
                "schema": DELTA_SCHEMA,
                "base_digest": self.base_digest,
                "target_digest": self.target_digest,
                "prefix_items": self.prefix_items,
                "appended": [_encode_item(item) for item in self.appended],
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        """Decode and validate one serialized delta."""

        payload = _load_json_object(data, _DELTA_FIELDS)
        if payload["schema"] != DELTA_SCHEMA:
            raise ContextPackError("unsupported context pack delta schema")
        base_digest = payload["base_digest"]
        target_digest = payload["target_digest"]
        if not isinstance(base_digest, str) or not _is_digest(base_digest):
            raise ContextPackError("context pack delta base_digest is invalid")
        if not isinstance(target_digest, str) or not _is_digest(target_digest):
            raise ContextPackError("context pack delta target_digest is invalid")
        prefix_items = payload["prefix_items"]
        if not isinstance(prefix_items, int) or isinstance(prefix_items, bool) or prefix_items < 0:
            raise ContextPackError("context pack delta prefix_items must be a non-negative integer")
        appended = _decode_items(payload["appended"])
        return cls(
            base_digest=base_digest,
            target_digest=target_digest,
            prefix_items=prefix_items,
            appended=appended,
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Rebuild the target pack from a base pack and one delta."""

    if base.digest != delta.base_digest:
        raise ContextPackError("context pack delta base digest does not match the base pack")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("context pack delta prefix_items exceeds the base pack")
    target = ContextPack.build(base.items[: delta.prefix_items] + delta.appended)
    if target.digest != delta.target_digest:
        raise ContextPackError("context pack delta target digest does not match the rebuilt pack")
    return target


def _chunk_utf8(raw: bytes, chunk_bytes: int) -> tuple[bytes, ...]:
    if not raw:
        return ()
    text = raw.decode("utf-8")
    chunks: list[bytes] = []
    current = bytearray()
    for character in text:
        encoded = character.encode("utf-8")
        if len(encoded) > chunk_bytes:
            raise ContextPackError("a single UTF-8 character exceeds the chunk byte limit")
        if current and len(current) + len(encoded) > chunk_bytes:
            chunks.append(bytes(current))
            current = bytearray()
        current.extend(encoded)
    if current:
        chunks.append(bytes(current))
    return tuple(chunks)


def _validate_items(items: tuple[bytes, ...]) -> tuple[bytes, ...]:
    if not isinstance(items, tuple):
        raise ContextPackError("context pack items must be a tuple of bytes")
    normalized: list[bytes] = []
    for item in items:
        if not isinstance(item, bytes):
            raise ContextPackError("context pack items must contain bytes")
        normalized.append(item)
    return tuple(normalized)


def _encode_item(item: bytes) -> str:
    return base64.b64encode(item).decode("ascii")


def _decode_items(values: Any) -> tuple[bytes, ...]:
    if not isinstance(values, list):
        raise ContextPackError("context pack items must be a JSON array")
    items: list[bytes] = []
    for value in values:
        if not isinstance(value, str):
            raise ContextPackError("context pack items must be Base64 strings")
        try:
            items.append(base64.b64decode(value, validate=True))
        except (binascii.Error, ValueError) as error:
            raise ContextPackError("context pack items must be valid Base64") from error
    return tuple(items)


def _pack_digest(items: tuple[bytes, ...]) -> str:
    canonical = _canonical_json(
        {"schema": PACK_SCHEMA, "items": [_encode_item(item) for item in items]}
    )
    return _DIGEST_PREFIX + hashlib.sha256(canonical).hexdigest()


def _pack_payload(items: tuple[bytes, ...], digest: str, byte_count: int) -> bytes:
    return _canonical_json(
        {
            "schema": PACK_SCHEMA,
            "items": [_encode_item(item) for item in items],
            "digest": digest,
            "byte_count": byte_count,
        }
    )


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _load_json_object(data: bytes, fields: frozenset[str]) -> dict[str, Any]:
    if not isinstance(data, bytes):
        raise ContextPackError("context pack payload must be bytes")
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContextPackError("context pack payload must be valid UTF-8 JSON") from error
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ContextPackError("context pack payload has missing or unexpected fields")
    return payload


def _is_digest(value: str) -> bool:
    if not value.startswith(_DIGEST_PREFIX):
        return False
    hex_part = value[len(_DIGEST_PREFIX) :]
    if len(hex_part) != 64 or hex_part != hex_part.lower():
        return False
    try:
        int(hex_part, 16)
    except ValueError:
        return False
    return True


__all__ = [
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
