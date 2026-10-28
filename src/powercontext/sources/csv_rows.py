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

"""Row-level CSV Sources with exact record and citation identity.

Each data record becomes one captured Source so that a retrieval citation can be
resolved back to the complete original row, independent of row order and of any
later edits to non-key columns.
"""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

from pydantic import BaseModel
from pydantic import JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

CSV_ROW_SOURCE_NAME = "csv-row"
_CSV_ROW_ID_PREFIX = "csv"
_BOM = b"\xef\xbb\xbf"


class CSVDocumentInput(BaseModel):
    """Complete offline CSV document bytes plus the row identity configuration."""

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One validated CSV data record, ready to be materialized as a Source."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """Captured Source for exactly one CSV record."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build the delimiter-safe row identity ``csv:<document>:<key value>``."""

    _validate_document_id(document_id)
    source_id = (
        f"{_CSV_ROW_ID_PREFIX}:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    )
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(
            f"csv row source id must not exceed {MAX_SOURCE_ID_LENGTH} characters"
        )
    return source_id


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse and validate one whole CSV document into row inputs.

    The document is validated atomically: any encoding, quoting, header, width or
    primary-key error raises ``ValueError`` and no partial row is returned.
    """

    _validate_document_id(value.document_id)
    if not value.primary_key or not value.primary_key.strip():
        raise ValueError("primary_key must be a non-empty column name")

    records = _read_records(value.content)
    if not records:
        raise ValueError("csv document has no header record")

    header = records[0]
    if not header:
        raise ValueError("csv header must not be empty")
    _validate_header(header)
    try:
        key_index = header.index(value.primary_key)
    except ValueError:
        raise ValueError(f"primary_key column {value.primary_key!r} is not present in the header") from None

    columns = tuple(header)
    rows: list[CSVRowInput] = []
    seen_keys: set[str] = set()
    for record in records[1:]:
        # Completely empty physical records after the header are ignored.
        if not record:
            continue
        if len(record) != len(columns):
            raise ValueError(
                f"csv record has {len(record)} fields but the header has {len(columns)}"
            )
        primary_key_value = record[key_index]
        if not primary_key_value or not primary_key_value.strip():
            raise ValueError("csv primary key value must not be empty or blank")
        if primary_key_value in seen_keys:
            raise ValueError(f"duplicate csv primary key value {primary_key_value!r}")
        seen_keys.add(primary_key_value)
        rows.append(
            CSVRowInput(
                document_id=value.document_id,
                primary_key=value.primary_key,
                primary_key_value=primary_key_value,
                columns=columns,
                values=tuple(record),
            )
        )
    return tuple(rows)


def _read_records(content: bytes) -> list[list[str]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("csv document must be valid UTF-8") from error
    if not text:
        raise ValueError("csv document is empty")
    stream = io.StringIO(text, newline="")
    try:
        records = list(csv.reader(stream, strict=True))
    except csv.Error as error:
        raise ValueError(f"malformed csv document: {error}") from error
    return records


def _validate_header(header: list[str]) -> None:
    if not header:
        raise ValueError("csv header must not be empty")
    seen: set[str] = set()
    for column in header:
        if not column or not column.strip():
            raise ValueError("csv header fields must not be empty or blank")
        if column in seen:
            raise ValueError(f"duplicate csv header column {column!r}")
        seen.add(column)


def _validate_document_id(document_id: str) -> None:
    if not document_id:
        raise ValueError("document_id must be non-empty")
    if document_id != document_id.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")


class CSVRowSourceAdapter:
    """Resolve and read captured CSV row Sources."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    """Expose the complete row as canonical text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = self._row_mapping(source)
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )

    @staticmethod
    def _row_mapping(source: CSVRowSource) -> dict[str, JsonValue]:
        return {column: value for column, value in zip(source.columns, source.values, strict=True)}


CSV_ROW_SOURCE_ADAPTER = CSVRowSourceAdapter()
CSV_ROW_TEXT_EVIDENCE_PROJECTION = CSVRowTextEvidenceProjection()
CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(
    CSV_ROW_SOURCE_ADAPTER,
    projections=(CSV_ROW_TEXT_EVIDENCE_PROJECTION,),
)
# The fixed identity of the row Source definition declared by the challenge.
CSV_ROW_DEFINITION_NAME = CSV_ROW_SOURCE_NAME
CSV_ROW_DEFINITION_VERSION = "1"

__all__ = [
    "CSV_ROW_DEFINITION_NAME",
    "CSV_ROW_DEFINITION_VERSION",
    "CSV_ROW_SOURCE_ADAPTER",
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_NAME",
    "CSV_ROW_TEXT_EVIDENCE_PROJECTION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "csv_row_source_id",
    "parse_csv_document",
]
