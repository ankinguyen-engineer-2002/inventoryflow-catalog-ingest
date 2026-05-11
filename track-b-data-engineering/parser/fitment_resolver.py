"""Fitment resolver — Python port of fitment-resolver.ts.

Year/make/model fitment is encoded in the SHEET NAME (the data file
doesn't have explicit fitment columns). Returns ResolvedFitment when at
least year OR model code can be extracted; returns None for sheets like
"TABLE OF CONTENTS", "CARBURETOR JETS", etc.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedFitment:
    year_start: int | None
    year_end: int | None
    model_code: str | None
    variant: str | None


_MODEL_CODE_RE = re.compile(
    r"\b(AY|AT|AU|KMB|TS|TSD|TD|TT|K2|K4|K6|KT|T2|T4|S70|S200|S350|eA|eKMB)\d*-?\d*[A-Z]?\b",
    re.IGNORECASE,
)
_RANGE_PAREN_RE = re.compile(r"\((\d{4})\s*-\s*(\d{4})\)")
_OPEN_END_PAREN_RE = re.compile(r"\((\d{4})\s*\+\)")
_SINGLE_YEAR_PAREN_RE = re.compile(r"\((\d{4})\)")
_OPEN_END_PREFIX_RE = re.compile(r"\b(\d{4})\+")
_VARIANT_RE = re.compile(r"\b(EPA|EFI)\b", re.IGNORECASE)


def resolve_fitment_from_sheet_name(sheet_name: str) -> ResolvedFitment | None:
    name = sheet_name.strip()
    if _is_non_fitment_sheet(name):
        return None

    year_start: int | None = None
    year_end: int | None = None

    range_m = _RANGE_PAREN_RE.search(name)
    if range_m:
        year_start = int(range_m.group(1))
        year_end = int(range_m.group(2))
    else:
        open_end = _OPEN_END_PAREN_RE.search(name)
        if open_end:
            year_start = int(open_end.group(1))
            year_end = None
        else:
            single = _SINGLE_YEAR_PAREN_RE.search(name)
            if single:
                year_start = int(single.group(1))
                year_end = int(single.group(1))
            else:
                prefix = _OPEN_END_PREFIX_RE.search(name)
                if prefix:
                    year_start = int(prefix.group(1))
                    year_end = None

    model_match = _MODEL_CODE_RE.search(name)
    model_code = model_match.group(0).upper() if model_match else None

    variant_match = _VARIANT_RE.search(name)
    variant = variant_match.group(1).upper() if variant_match else None

    if year_start is None and model_code is None:
        return None

    return ResolvedFitment(
        year_start=year_start,
        year_end=year_end,
        model_code=model_code,
        variant=variant,
    )


def _is_non_fitment_sheet(name: str) -> bool:
    upper = name.upper()
    if upper in {
        "TABLE OF CONTENTS",
        "TOC",
        "SHEET18",
        "CARBURETOR JETS",
        "FORK SEAL SPECS",
        "ATV WHEEL SPECS",
        "SPOKE SPECS",
        "SPARK PLUGS",
        "BATTERY SPECS",
        "OWNERS MANUALS",
        "SNOW TRACK KIT",
        "SKI KIT PARTS",
        "EC2I",
    }:
        return True
    if upper.startswith("DIRTBIKEPITBIKE WHEEL BEARING"):
        return True
    if "UPGRADE KIT" in upper:
        return True
    return False
