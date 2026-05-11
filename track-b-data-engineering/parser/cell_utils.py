"""Cell-value coercion utilities — Python port of cell-utils.ts.

openpyxl in read-only mode returns cells as native Python types
(str | int | float | datetime | bool | None). RichText is flattened to
a plain string by openpyxl, so we don't need exceljs's richText branch.
Implementation kept symmetric so the API mirrors Track A.
"""

from __future__ import annotations

from datetime import date, datetime


def cell_to_string(v: object) -> str | None:
    """Coerce any openpyxl cell value to a clean string or None.

    Mirrors `cellToString` in track-a-jd-native/src/ingest/cell-utils.ts.
    Special-cases float values storing integers (Excel internally stores
    every numeric as a float — "1" in a cell comes back as 1.0). exceljs
    on the JS side renders these as "1"; we match that wire format.
    """
    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v).strip()
    return s if s else None


def cell_to_number(v: object) -> float | None:
    """Coerce any openpyxl cell value to a finite float or None.

    Mirrors `cellToNumber` in track-a-jd-native/src/ingest/cell-utils.ts.
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        f = float(v)
        return f if _is_finite(f) else None
    s = str(v).strip()
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if _is_finite(f) else None


def _is_finite(f: float) -> bool:
    return f == f and f not in (float("inf"), float("-inf"))
