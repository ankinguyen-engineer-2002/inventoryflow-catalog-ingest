"""Row normaliser — Python port of row-normalizer.ts.

Converts raw cell tuples from a detected section into validated
`NormalisedRow` shapes. Encodes the same tolerance for messy OEM data
that Track A applies: callout polymorphism, multi-space artefacts in CN
cells, best-effort numeric coercion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .cell_utils import cell_to_number, cell_to_string
from .section_detector import DetectedSection, SheetRow


@dataclass(frozen=True)
class CalloutNumber:
    raw: str
    parent: int | None
    sub: int | None
    variant: str | None


@dataclass(frozen=True)
class NormalisedRow:
    source_row_index: int
    source_sheet: str
    callout: CalloutNumber | None
    part_number: str
    part_number_alias: str | None
    name_en: str | None
    name_cn: str | None
    spec_cn: str | None
    qty_per_vehicle: float | None
    dealer_cost: float | None
    unit: str | None
    retail_price: float | None


@dataclass(frozen=True)
class _Reject:
    row_index: int
    reason: str


def normalise_row(
    row: SheetRow, section: DetectedSection, sheet_name: str
) -> NormalisedRow | _Reject:
    """Normalise one data row.

    Returns NormalisedRow on success, _Reject on missing part number.
    Mirrors `normaliseRow` in row-normalizer.ts.
    """

    def get(col: str) -> object:
        idx = section.column_map.get(col)
        if idx is None or idx >= len(row.values):
            return None
        return row.values[idx]

    part_number = cell_to_string(get(section.signature.part_number_column))
    if part_number is None and section.kind == "engine":
        part_number = cell_to_string(get("OLD PART NUMBER"))

    if not part_number:
        return _Reject(row_index=row.row_index, reason="missing_part_number")

    # Reject part numbers that look like a column-header label leaking
    # into the data range (case-insensitive). Observed: "U8 CODE" when a
    # chassis_u8 section has its header repeated as a stray data row.
    if _looks_like_header_label(part_number, section.signature.columns):
        return _Reject(row_index=row.row_index, reason="header_label_in_data")

    part_number_alias: str | None = None
    if section.kind == "engine":
        old = cell_to_string(get("OLD PART NUMBER"))
        if old and old != part_number:
            part_number_alias = old

    return NormalisedRow(
        source_row_index=row.row_index,
        source_sheet=sheet_name.strip(),
        callout=parse_callout(get("No.")),
        part_number=part_number,
        part_number_alias=part_number_alias,
        name_en=cell_to_string(get("EN name")),
        name_cn=_clean_cn_string(get("CN name")),
        spec_cn=_clean_cn_string(get("Specifications in CN")),
        qty_per_vehicle=cell_to_number(get("Qty/vehicle")),
        dealer_cost=cell_to_number(get("Dealer")),
        unit=cell_to_string(get("QTY")),
        retail_price=cell_to_number(get("Retail")),
    )


_CALLOUT_PLAIN_RE = re.compile(r"^(\d+)(?:\.0+)?$")
_CALLOUT_SUB_RE = re.compile(r"^(\d+)-(\d+)([A-Z]?)$", re.IGNORECASE)


def parse_callout(v: object) -> CalloutNumber | None:
    if v is None:
        return None
    raw = str(v).strip()
    if not raw:
        return None

    # openpyxl returns Excel-stored integers as floats ("1.0"). Track A's
    # exceljs returns them as "1". Normalise so the wire format matches.
    if isinstance(v, float) and v.is_integer():
        raw = str(int(v))

    m = _CALLOUT_PLAIN_RE.match(raw)
    if m:
        return CalloutNumber(raw=raw, parent=int(m.group(1)), sub=None, variant=None)

    if isinstance(v, (int, float)) and not isinstance(v, bool):
        try:
            f = float(v)
        except (TypeError, ValueError):
            f = float("nan")
        if f == f:  # not NaN
            return CalloutNumber(raw=raw, parent=int(f), sub=None, variant=None)

    m = _CALLOUT_SUB_RE.match(raw)
    if m:
        return CalloutNumber(
            raw=raw,
            parent=int(m.group(1)),
            sub=int(m.group(2)),
            variant=m.group(3).upper() if m.group(3) else None,
        )

    return CalloutNumber(raw=raw, parent=None, sub=None, variant=None)


def _looks_like_header_label(value: str, columns: tuple[str, ...]) -> bool:
    upper = value.strip().upper()
    return any(upper == col.upper() for col in columns)


_MULTISPACE_RE = re.compile(r"\s{2,}")


def _clean_cn_string(v: object) -> str | None:
    s = cell_to_string(v)
    if s is None:
        return None
    return _MULTISPACE_RE.sub(" ", s)
