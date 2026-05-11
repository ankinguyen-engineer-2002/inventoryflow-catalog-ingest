"""Tests for the Python-ported section detector.

Mirrors track-a-jd-native/test/unit/section-detector.test.ts to validate
that the Track B port preserves Track A's matching behaviour. Running
both suites against a shared fixture set is the regression guard against
silent divergence between tracks.
"""

from __future__ import annotations

import pytest

from dagster_project.section_detector import SIGNATURES, match_header


@pytest.fixture
def chassis_header() -> list[str]:
    return [
        "No.", "Part Number", "EN name", "CN name",
        "Specifications in CN", "Qty/vehicle", "Dealer", "QTY", "Retail",
    ]


@pytest.fixture
def engine_header() -> list[str]:
    return [
        "No.", "OLD PART NUMBER", "NEW PART NUMBER", "EN name", "CN name",
        "Qty/vehicle", "Dealer", "QTY", "Retail",
    ]


@pytest.fixture
def u8_header() -> list[str]:
    return [
        "No.", "U8 Code", "Model", "EN name", "CN name",
        "Specifications in CN", "Qty/vehicle", "Dealer", "QTY", "Retail",
    ]


class TestMatchHeader:
    def test_matches_chassis(self, chassis_header: list[str]) -> None:
        sig = match_header(chassis_header)
        assert sig is not None
        assert sig.kind == "chassis"
        assert sig.part_number_column == "Part Number"

    def test_matches_engine(self, engine_header: list[str]) -> None:
        sig = match_header(engine_header)
        assert sig is not None
        assert sig.kind == "engine"
        assert sig.part_number_column == "NEW PART NUMBER"

    def test_matches_u8(self, u8_header: list[str]) -> None:
        sig = match_header(u8_header)
        assert sig is not None
        assert sig.kind == "chassis_u8"
        assert sig.part_number_column == "U8 Code"

    def test_rejects_data_row(self) -> None:
        data_row = ["1", "602006-0015", "black grip", "把套", "spec", "2", "6", "/ea", "10.2"]
        assert match_header(data_row) is None

    def test_tolerates_whitespace(self) -> None:
        sig = match_header(["No.  ", " Part Number", "EN name", "CN name"])
        assert sig is not None
        assert sig.kind == "chassis"

    def test_handles_none_cells(self) -> None:
        sig = match_header([None, "No.", "Part Number", "EN name", "CN name"])
        assert sig is not None
        assert sig.kind == "chassis"

    def test_returns_none_when_required_missing(self) -> None:
        # Missing EN name + CN name
        assert match_header(["No.", "Part Number", "Qty/vehicle", "Dealer"]) is None


class TestSignatures:
    def test_three_signatures_registered(self) -> None:
        kinds = {s.kind for s in SIGNATURES}
        assert kinds == {"chassis", "engine", "chassis_u8"}

    def test_required_columns_are_subset_of_columns(self) -> None:
        for sig in SIGNATURES:
            assert set(sig.required).issubset(sig.columns), (
                f"{sig.kind}: required columns must be a subset of columns"
            )
