from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import MultiPolygon, box

from app.parsers.clc_parser import (
    inspect_geopackage,
    iter_geopackage_batches,
    parse_geopackage,
)


def _write_source(path, *, crs="EPSG:3035", fields=None):
    values = {
        "ID": [1],
        "Site_id": [131],
        "LC_code18": [31],
        "Ref_year": [2018],
        "Shape_Area": [100.0],
    }
    if fields:
        values = fields
    frame = gpd.GeoDataFrame(
        values,
        geometry=[MultiPolygon([box(0, 0, 10, 10)])],
        crs=crs,
    )
    frame.to_file(path, layer="validation", driver="GPKG")
    return path


def test_missing_required_field_is_file_level_fatal(tmp_path):
    path = _write_source(
        tmp_path / "missing.gpkg",
        fields={
            "ID": [1],
            "Site_id": [131],
            "LC_code18": [31],
            "Ref_year": [2018],
            "geometry": [MultiPolygon([box(0, 0, 10, 10)])],
        },
    )
    with pytest.raises(ValueError, match="required source fields are missing"):
        parse_geopackage(path, expected_sha256=None, expected_layer="validation")


def test_missing_crs_is_file_level_fatal(tmp_path):
    path = _write_source(tmp_path / "missing-crs.gpkg", crs=None)
    with pytest.raises(ValueError, match="expected EPSG:3035"):
        parse_geopackage(path, expected_sha256=None, expected_layer="validation")


def test_valid_unsupported_crs_is_rejected_without_reprojection(tmp_path):
    path = _write_source(tmp_path / "wrong-crs.gpkg", crs="EPSG:4326")
    with pytest.raises(ValueError, match="expected EPSG:3035"):
        parse_geopackage(path, expected_sha256=None, expected_layer="validation")


def test_malformed_source_crs_metadata_is_file_level_fatal(tmp_path, monkeypatch):
    path = _write_source(tmp_path / "malformed-crs.gpkg")
    original_read_file = gpd.read_file

    class MalformedCrs:
        def to_epsg(self):
            raise ValueError("malformed CRS")

    class FrameWithMalformedCrs:
        def __init__(self, frame):
            self._frame = frame
            self.columns = frame.columns
            self.crs = MalformedCrs()

        def __len__(self):
            return len(self._frame)

        def itertuples(self, **kwargs):
            return self._frame.itertuples(**kwargs)

    monkeypatch.setattr(
        gpd,
        "read_file",
        lambda *args, **kwargs: FrameWithMalformedCrs(original_read_file(*args, **kwargs)),
    )
    with pytest.raises(ValueError, match="source CRS metadata is malformed"):
        parse_geopackage(path, expected_sha256=None, expected_layer="validation")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ID", "bad-id"),
        ("Site_id", "bad-site"),
        ("LC_code18", "bad-code"),
        ("Shape_Area", "bad-area"),
    ],
)
def test_malformed_record_is_rejected_and_other_records_can_continue(tmp_path, field, value):
    values = {
        "ID": [1],
        "Site_id": [131],
        "LC_code18": [31],
        "Ref_year": [2018],
        "Shape_Area": [100.0],
    }
    values[field] = [value]
    path = _write_source(tmp_path / f"bad-{field}.gpkg", fields=values)
    result = parse_geopackage(path, expected_sha256=None, expected_layer="validation")
    assert len(result.records) == 0
    assert len(result.rejected) == 1
    assert result.rejected[0].reason in {
        "malformed_identifier",
        "malformed_area",
        "source_area_non_positive",
    }


def test_bounded_reader_yields_multiple_batches_and_final_partial_batch(tmp_path):
    path = tmp_path / "chunked.gpkg"
    geometries = [MultiPolygon([box(index * 10, 0, index * 10 + 10, 10)]) for index in range(5)]
    frame = gpd.GeoDataFrame(
        {
            "ID": [1, 2, 3, 4, 5],
            "Site_id": [131] * 5,
            "LC_code18": [31] * 5,
            "Ref_year": [2018] * 5,
            "Shape_Area": [100.0] * 5,
        },
        geometry=geometries,
        crs="EPSG:3035",
    )
    frame.to_file(path, layer="validation", driver="GPKG")

    source = inspect_geopackage(
        path, expected_sha256=None, expected_layer="validation"
    )
    batches = list(iter_geopackage_batches(source, batch_size=2))

    assert source.source_feature_count == 5
    assert [batch.source_feature_count for batch in batches] == [2, 2, 1]
    assert [
        record.row_number for batch in batches for record in batch.records
    ] == [1, 2, 3, 4, 5]
    assert [
        record.source_id for batch in batches for record in batch.records
    ] == [1, 2, 3, 4, 5]


def test_bounded_reader_rejects_nonpositive_batch_size(tmp_path):
    path = _write_source(tmp_path / "batch-size.gpkg")
    source = inspect_geopackage(
        path, expected_sha256=None, expected_layer="validation"
    )
    with pytest.raises(ValueError, match="batch size must be a positive integer"):
        list(iter_geopackage_batches(source, batch_size=0))
