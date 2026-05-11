"""Python port of Track A's section detector.

Track A's `src/ingest/section-detector.ts` recognises the three header
signatures observed in the Kayo sample. This Python port mirrors that
behaviour so Track B's silver layer can produce the same shape without
re-implementing parsing logic.

Only the matching logic is ported; the cell-richtext coercion is handled
by Polars natively when reading the source xlsx.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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


def match_header(cells: list[str | None]) -> HeaderSignature | None:
    """Try to match a row of cell strings against any known signature.

    Returns the matched signature, or None if no signature satisfies its
    `required` column set.

    Mirrors `matchHeader` in track-a-jd-native/src/ingest/section-detector.ts.
    """
    trimmed = [(c or "").strip() for c in cells]
    cell_set = set(trimmed)

    for sig in SIGNATURES:
        if all(req in cell_set for req in sig.required):
            return sig
    return None
