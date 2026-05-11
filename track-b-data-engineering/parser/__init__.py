"""Track A parser, ported to Python.

These modules mirror the canonical TypeScript parser in
`track-a-jd-native/src/ingest/`. Track B reuses them so the silver layer
produces the same logical output as Track A given the same input xlsx —
which is what makes "same problem, two infrastructures, same output"
actually true.

Modules:
    cell_utils         — cell-value coercion (mirrors cell-utils.ts)
    section_detector   — header signature matching + section discovery
                         (mirrors section-detector.ts)
    row_normalizer     — typed row extraction with callout parsing
                         (mirrors row-normalizer.ts)
    fitment_resolver   — derive year/model/variant from sheet name
                         (mirrors fitment-resolver.ts)
    parse_xlsx         — top-level glue: read sheets → sections → rows
                         → list of NormalisedRow + fitment
"""

from .cell_utils import cell_to_number, cell_to_string
from .fitment_resolver import ResolvedFitment, resolve_fitment_from_sheet_name
from .row_normalizer import CalloutNumber, NormalisedRow, normalise_row, parse_callout
from .section_detector import (
    SIGNATURES,
    DetectedSection,
    HeaderSignature,
    detect_sections,
    match_header,
)

__all__ = [
    "CalloutNumber",
    "DetectedSection",
    "HeaderSignature",
    "NormalisedRow",
    "ResolvedFitment",
    "SIGNATURES",
    "cell_to_number",
    "cell_to_string",
    "detect_sections",
    "match_header",
    "normalise_row",
    "parse_callout",
    "resolve_fitment_from_sheet_name",
]
