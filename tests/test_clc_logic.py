from __future__ import annotations

import pytest
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, box

from app.clc_contract import (
    ABSOLUTE_AREA_TOLERANCE_M2,
    CLC_VECTOR_CLASS_LABELS,
    RELATIVE_AREA_TOLERANCE,
    RecordValidationError,
    build_record,
    classify_identities,
)
from app.parsers.clc_parser import parse_geopackage


def attributes(**overrides):
    values = {
        "ID": 1,
        "Site_id": 131,
        "LC_code18": 31,
        "Ref_year": 2018,
        "Shape_Area": 1.0,
        "extra": "preserved",
    }
    values.update(overrides)
    return values


def geometry(size=1.0):
    return MultiPolygon([box(0, 0, size, size)])


def test_malformed_required_identifier_is_rejected():
    with pytest.raises(RecordValidationError, match="ID is missing"):
        build_record(1, attributes(ID=None), geometry())


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("ID", "not-an-integer", "malformed_identifier"),
        ("Site_id", 1.5, "malformed_identifier"),
        ("LC_code18", "not-a-code", "malformed_identifier"),
        ("Ref_year", "not-a-year", "malformed_identifier"),
        ("Shape_Area", "not-a-number", "malformed_area"),
    ],
)
def test_malformed_required_values_have_stable_reasons(field, value, reason):
    with pytest.raises(RecordValidationError) as error:
        build_record(1, attributes(**{field: value}), geometry())
    assert error.value.reason == reason


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -float("inf"), 0.0, -1.0])
def test_null_nonfinite_zero_and_negative_source_areas_are_handled(value):
    if value is None:
        record = build_record(1, attributes(Shape_Area=None), geometry())
        assert record.source_shape_area is None
    else:
        with pytest.raises(RecordValidationError) as error:
            build_record(1, attributes(Shape_Area=value), geometry())
        assert error.value.reason == "source_area_non_positive"


def test_null_empty_and_invalid_geometries_are_rejected_without_repair():
    with pytest.raises(RecordValidationError, match="geometry is missing"):
        build_record(1, attributes(), None)
    with pytest.raises(RecordValidationError) as empty_error:
        build_record(1, attributes(), MultiPolygon())
    assert empty_error.value.reason == "empty_geometry"

    bowtie = Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])
    invalid_multi = MultiPolygon([bowtie])
    with pytest.raises(RecordValidationError) as invalid_error:
        build_record(1, attributes(), invalid_multi)
    assert invalid_error.value.reason == "invalid_geometry"
    assert invalid_multi.equals(MultiPolygon([bowtie]))


@pytest.mark.parametrize("unsupported", [box(0, 0, 1, 1), GeometryCollection([box(0, 0, 1, 1)])])
def test_polygon_and_geometry_collection_are_rejected_by_strict_source_contract(unsupported):
    with pytest.raises(RecordValidationError) as error:
        build_record(1, attributes(), unsupported)
    assert error.value.reason == "geometry_type"


def test_multipolygon_is_accepted_without_geometry_normalization():
    source = geometry(size=2.0)
    record = build_record(1, attributes(Shape_Area=source.area), source)
    assert record.calculated_area_m2 == source.area
    assert record.geometry_wkb


def test_nodata_and_unknown_codes_are_distinguished():
    nodata = build_record(1, attributes(LC_code18=254), geometry())
    unknown = build_record(2, attributes(ID=2, LC_code18=999), geometry())
    assert nodata.quality_status == "nodata"
    assert unknown.quality_status == "unknown_code"
    assert nodata.lc_code18 == 254
    assert unknown.lc_code18 == 999


def test_authoritative_vector_labels_and_nodata_are_distinct():
    assert len(CLC_VECTOR_CLASS_LABELS) == 18
    assert all(CLC_VECTOR_CLASS_LABELS.values())
    labeled = build_record(1, attributes(LC_code18=21), geometry())
    nodata = build_record(2, attributes(ID=2, LC_code18=254), geometry())
    unknown = build_record(3, attributes(ID=3, LC_code18=999), geometry())
    assert labeled.class_label == "Pure needle leaved"
    assert nodata.class_label is None
    assert nodata.quality_status == "nodata"
    assert unknown.class_label is None
    assert unknown.quality_status == "unknown_code"


def test_all_authoritatively_mapped_codes_are_accepted():
    for index, code in enumerate(sorted(CLC_VECTOR_CLASS_LABELS), start=1):
        record = build_record(
            index,
            attributes(ID=index, LC_code18=code),
            geometry(),
        )
        assert record.quality_status == "accepted"
        assert record.class_label == CLC_VECTOR_CLASS_LABELS[code]


def test_configured_checksum_mismatch_is_rejected(tmp_path):
    input_path = tmp_path / "not-a-geopackage.gpkg"
    input_path.write_bytes(b"not a geopackage")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        parse_geopackage(input_path, expected_sha256="0" * 64)


def test_area_absolute_tolerance_boundary():
    source_area = 1.0 + ABSOLUTE_AREA_TOLERANCE_M2 - 1e-9
    build_record(1, attributes(Shape_Area=source_area), geometry())
    with pytest.raises(RecordValidationError, match="area difference"):
        build_record(
            1,
            attributes(Shape_Area=1.0 + ABSOLUTE_AREA_TOLERANCE_M2 + 1e-6),
            geometry(),
        )


def test_area_relative_tolerance_boundary():
    calculated_area = 1_000_000.0
    source_area = calculated_area / (1.0 + RELATIVE_AREA_TOLERANCE)
    build_record(
        1,
        attributes(Shape_Area=source_area),
        geometry(size=1000.0),
    )
    with pytest.raises(RecordValidationError, match="area difference"):
        build_record(
            1,
            attributes(Shape_Area=calculated_area / (1.0 + RELATIVE_AREA_TOLERANCE + 1e-8)),
            geometry(size=1000.0),
        )


def test_exact_duplicate_is_collapsed_and_conflict_is_reported():
    first = build_record(1, attributes(), geometry())
    duplicate = build_record(2, attributes(), geometry())
    duplicate_conflict = build_record(3, attributes(ID=1, extra="changed"), geometry())

    exact = classify_identities([first, duplicate])
    assert len(exact.unique_records) == 1
    assert exact.duplicate_count == 1
    assert not exact.conflicts

    conflict = classify_identities([first, duplicate_conflict])
    assert len(conflict.conflicts) == 1


def test_volatile_attributes_do_not_change_fingerprint():
    first = build_record(
        1,
        attributes(run_id="first", local_path="C:/one/input.gpkg"),
        geometry(),
    )
    second = build_record(
        2,
        attributes(run_id="second", local_path="D:/two/input.gpkg"),
        geometry(),
    )
    assert first.record_fingerprint == second.record_fingerprint
