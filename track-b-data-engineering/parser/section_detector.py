"""Section detector — Python port of section-detector.ts.

Each parts sheet is multi-section: 10–20 sections per sheet, each consisting
of (image, title row, header row, data rows, blank rows). The header row
repeats within a single sheet — that repetition is the key signal we use.

We don't iterate from a fixed row index. We scan all rows, recognise any
row whose trimmed cell values match a known header signature, and treat
that row as a section boundary.

See ADR-005 for the design reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .cell_utils import cell_to_string

HeaderKind = Literal["chassis", "engine", "chassis_u8"]


@dataclass(frozen=True)
class HeaderSignature:
    """A header signature: the set of column labels we recognise."""

    kind: HeaderKind
    columns: tuple[str, ...]
    required: tuple[str, ...]
    part_number_column: str


SIGNATURES: tuple[HeaderSignature, ...] = (
    HeaderSignature(
        kind="chassis",
        columns=(
            "No.", "Part Number", "EN name", "CN name",
            "Specifications in CN", "Qty/vehicle", "Dealer", "QTY", "Retail",
        ),
        required=("No.", "Part Number", "EN name", "CN name"),
        part_number_column="Part Number",
    ),
    HeaderSignature(
        kind="engine",
        columns=(
            "No.", "OLD PART NUMBER", "NEW PART NUMBER", "EN name", "CN name",
            "Qty/vehicle", "Dealer", "QTY", "Retail",
        ),
        required=("OLD PART NUMBER", "NEW PART NUMBER", "EN name", "CN name"),
        part_number_column="NEW PART NUMBER",
    ),
    HeaderSignature(
        kind="chassis_u8",
        columns=(
            "No.", "U8 Code", "Model", "EN name", "CN name",
            "Specifications in CN", "Qty/vehicle", "Dealer", "QTY", "Retail",
        ),
        required=("No.", "U8 Code", "EN name", "CN name"),
        part_number_column="U8 Code",
    ),
)


@dataclass(frozen=True)
class SheetRow:
    """One row read from a sheet.

    `row_index` is 1-based as it appears in the xlsx. `values` is 1-based
    by column with index 0 = None (kept for off-by-one symmetry with the
    Track A reader).
    """

    row_index: int
    values: tuple[object, ...]


@dataclass(frozen=True)
class DetectedSection:
    kind: HeaderKind
    signature: HeaderSignature
    header_row_index: int
    data_start_index: int
    data_end_index: int
    title: str | None
    column_map: dict[str, int]


def match_header(row: SheetRow) -> tuple[HeaderSignature, dict[str, int]] | None:
    """Try to match a row of cell values against any known signature.

    Returns (signature, column_map) when a match exists, else None.
    Mirrors `matchHeader` in section-detector.ts.
    """
    trimmed = [cell_to_string(v) or "" for v in row.values]

    for sig in SIGNATURES:
        cm: dict[str, int] = {}
        for col in sig.columns:
            try:
                idx = trimmed.index(col)
            except ValueError:
                continue
            cm[col] = idx
        if all(req in cm for req in sig.required):
            return sig, cm
    return None


def detect_sections(rows: list[SheetRow]) -> list[DetectedSection]:
    """Scan rows, detect section boundaries, return sections.

    Mirrors `detectSections` in section-detector.ts.
    """
    headers: list[tuple[SheetRow, HeaderSignature, dict[str, int]]] = []
    for row in rows:
        match = match_header(row)
        if match is not None:
            sig, cm = match
            headers.append((row, sig, cm))

    if not headers:
        return []

    by_index: dict[int, SheetRow] = {r.row_index: r for r in rows}
    last_row_index = rows[-1].row_index

    sections: list[DetectedSection] = []
    for i, (row, sig, cm) in enumerate(headers):
        next_header = headers[i + 1] if i + 1 < len(headers) else None
        data_start = row.row_index + 1
        data_end = next_header[0].row_index - 1 if next_header else last_row_index
        title = _find_title_above(by_index, row.row_index)
        sections.append(
            DetectedSection(
                kind=sig.kind,
                signature=sig,
                header_row_index=row.row_index,
                data_start_index=data_start,
                data_end_index=data_end,
                title=title,
                column_map=cm,
            )
        )

    return sections


_MAX_LOOKBACK = 4
_TITLE_MAX_FILLED_CELLS = 3


def _find_title_above(by_index: dict[int, SheetRow], header_row_index: int) -> str | None:
    for offset in range(1, _MAX_LOOKBACK + 1):
        candidate = by_index.get(header_row_index - offset)
        if candidate is None:
            continue
        if match_header(candidate) is not None:
            return None  # hit another header — no title for this section
        filled = [v for v in candidate.values if v is not None and str(v).strip() != ""]
        if not filled:
            continue
        if len(filled) > _TITLE_MAX_FILLED_CELLS:
            continue
        first = _first_non_empty(candidate.values)
        if first is None:
            continue
        if _looks_like_part_number_or_callout(first):
            continue
        return first
    return None


def _first_non_empty(values: tuple[object, ...]) -> str | None:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


import re

_PLAIN_NUM_RE = re.compile(r"^\d+(\.\d+)?$")
_SUB_NUM_RE = re.compile(r"^\d+-\d+[A-Z]?$")
_OEM_PN_RE = re.compile(r"^\d{3,6}-\d{4,}")


def _looks_like_part_number_or_callout(s: str) -> bool:
    if _PLAIN_NUM_RE.match(s):
        return True
    if _SUB_NUM_RE.match(s):
        return True
    if _OEM_PN_RE.match(s):
        return True
    return False
