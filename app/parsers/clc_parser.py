from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterator
from typing import Any

import geopandas as gpd
import pyogrio
from pyproj import CRS

from app.clc_contract import (
    CanonicalRecord,
    EXPECTED_CRS_EPSG,
    EXPECTED_FIXTURE_SHA256,
    EXPECTED_LAYER,
    REQUIRED_SOURCE_FIELDS,
    RejectedRecord,
    RecordValidationError,
    build_record,
    sha256_file,
)


@dataclass(frozen=True)
class ParseResult:
    input_path: str
    input_sha256: str
    source_layer: str
    source_feature_count: int
    records: tuple[CanonicalRecord, ...]
    rejected: tuple[RejectedRecord, ...]


@dataclass(frozen=True)
class SourceInspection:
    path: Path
    input_sha256: str
    source_layer: str
    source_feature_count: int
    crs: str
    geometry_type: str
    fields: tuple[str, ...]


def inspect_geopackage(
    input_path: str | Path,
    expected_sha256: str = EXPECTED_FIXTURE_SHA256,
    expected_layer: str = EXPECTED_LAYER,
) -> SourceInspection:
    path = Path(input_path)
    if not path.is_file():
        raise FileNotFoundError(path)

    input_sha256 = sha256_file(str(path))
    if expected_sha256 and input_sha256.lower() != expected_sha256.lower():
        raise ValueError(
            f"input SHA-256 mismatch: expected {expected_sha256}, got {input_sha256}"
        )

    layer_names = [str(row[0]) for row in pyogrio.list_layers(str(path))]
    if expected_layer not in layer_names:
        raise ValueError(
            f"required source layer {expected_layer!r} not found; available={layer_names!r}"
        )

    info = pyogrio.read_info(str(path), layer=expected_layer)
    fields = tuple(str(field) for field in info["fields"])
    missing = sorted(REQUIRED_SOURCE_FIELDS - (set(fields) | {"geometry"}))
    if missing:
        raise ValueError(f"required source fields are missing: {missing}")

    source_crs = info.get("crs")
    if not source_crs:
        raise ValueError(f"expected EPSG:{EXPECTED_CRS_EPSG}, got missing source CRS")
    try:
        source_epsg = CRS.from_user_input(source_crs).to_epsg()
    except Exception as exc:
        raise ValueError("source CRS metadata is malformed") from exc
    if source_epsg != EXPECTED_CRS_EPSG:
        raise ValueError(f"expected EPSG:{EXPECTED_CRS_EPSG}, got {source_crs}")

    return SourceInspection(
        path=path,
        input_sha256=input_sha256,
        source_layer=expected_layer,
        source_feature_count=int(info["features"]),
        crs=str(source_crs),
        geometry_type=str(info.get("geometry_type") or "unknown"),
        fields=fields,
    )


def iter_geopackage_batches(
    source: SourceInspection,
    batch_size: int,
) -> Iterator[ParseResult]:
    if batch_size <= 0:
        raise ValueError("batch size must be a positive integer")

    for offset in range(0, source.source_feature_count, batch_size):
        frame = pyogrio.read_dataframe(
            str(source.path),
            layer=source.source_layer,
            skip_features=offset,
            max_features=batch_size,
            use_arrow=False,
        )
        if frame.empty:
            raise ValueError(
                f"source read stopped at feature offset {offset} before "
                f"expected count {source.source_feature_count}"
            )
        yield _parse_frame(
            frame,
            input_path=source.path.name,
            input_sha256=source.input_sha256,
            source_layer=source.source_layer,
            row_offset=offset,
        )


def parse_geopackage(
    input_path: str | Path,
    expected_sha256: str = EXPECTED_FIXTURE_SHA256,
    expected_layer: str = EXPECTED_LAYER,
) -> ParseResult:
    source = inspect_geopackage(input_path, expected_sha256, expected_layer)
    frame = gpd.read_file(str(source.path), layer=source.source_layer)
    return _parse_frame(
        frame,
        input_path=source.path.name,
        input_sha256=source.input_sha256,
        source_layer=source.source_layer,
        row_offset=0,
    )


def _parse_frame(
    frame: gpd.GeoDataFrame,
    *,
    input_path: str,
    input_sha256: str,
    source_layer: str,
    row_offset: int,
) -> ParseResult:
    missing = sorted(REQUIRED_SOURCE_FIELDS - set(frame.columns))
    if missing:
        raise ValueError(f"required source fields are missing: {missing}")
    if frame.crs is None:
        raise ValueError(f"expected EPSG:{EXPECTED_CRS_EPSG}, got missing source CRS")
    try:
        source_epsg = frame.crs.to_epsg()
    except Exception as exc:
        raise ValueError("source CRS metadata is malformed") from exc
    if source_epsg != EXPECTED_CRS_EPSG:
        raise ValueError(f"expected EPSG:{EXPECTED_CRS_EPSG}, got {frame.crs}")

    records: list[CanonicalRecord] = []
    rejected: list[RejectedRecord] = []
    for row_number, row in enumerate(
        frame.itertuples(index=False, name=None), start=row_offset + 1
    ):
        row_values = dict(zip(frame.columns, row))
        geometry = row_values.pop("geometry", None)
        try:
            records.append(build_record(row_number, row_values, geometry))
        except RecordValidationError as exc:
            rejected.append(
                RejectedRecord(
                    row_number=row_number,
                    reason=exc.reason,
                    message=str(exc),
                    source_id=row_values.get("ID"),
                    site_id=row_values.get("Site_id"),
                    lc_code18=row_values.get("LC_code18"),
                )
            )

    return ParseResult(
        input_path=input_path,
        input_sha256=input_sha256,
        source_layer=source_layer,
        source_feature_count=len(frame),
        records=tuple(records),
        rejected=tuple(rejected),
    )
