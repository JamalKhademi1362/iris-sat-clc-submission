from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from typing import Any

import pandas as pd
from shapely import to_wkb
from shapely.geometry.base import BaseGeometry


SOURCE_PRODUCT = "CLCplus Backbone 2018"
PACKAGE_VERSION = "V1_1"
SOURCE_LAYER_NAME = "V1_0"
METADATA_EDITION = "01.00"
REFERENCE_YEAR = 2018
EXPECTED_CRS_EPSG = 3035
EXPECTED_LAYER = (
    "clms_clcplus_vector_2018_pu131_03035_v1_1__"
    "clms_clcplus_vector_2018_pu131_03035_v1_0"
)
EXPECTED_FIXTURE_SHA256 = (
    "5f7091f8d4d51c870f67aac8064f5a649b25dcfc898826cfc086ba9c163820db"
)

ABSOLUTE_AREA_TOLERANCE_M2 = 0.05
RELATIVE_AREA_TOLERANCE = 1e-4

REQUIRED_SOURCE_FIELDS = {
    "ID",
    "Site_id",
    "LC_code18",
    "Ref_year",
    "Shape_Area",
    "geometry",
}

# Codes observed in the verified PU131 sample; authoritative CLCplus
# Backbone 2018 class labels are defined below.
OBSERVED_LC_CODES = {21, 22, 31, 32, 33, 40, 51, 52, 53, 60, 100}
CLC_VECTOR_CLASS_LABELS = {
    11: "Very High Sealing Degree",
    12: "High Sealing Degree",
    21: "Pure needle leaved",
    22: "Dominantly needle leaved",
    31: "Pure broadleaved deciduous",
    32: "Pure broadleaved evergreen",
    33: "Dominantly broad leave",
    40: "Shrubland",
    51: "Permanent herbaceous without trees",
    52: "Permanent herbaceous with few trees",
    53: "Permanent herbaceous with many trees",
    60: "Periodically herbaceous",
    70: "Lichens and mosses",
    81: "Partly vegetated land - Low vegetation cover",
    82: "Partly vegetated land - Intermediate vegetation cover",
    90: "Non-vegetated land",
    100: "Water",
    110: "Snow and ice",
}
VOLATILE_FINGERPRINT_KEYS = {
    "run_id",
    "ingest_run_id",
    "loaded_at",
    "timestamp",
    "created_at",
    "updated_at",
    "local_path",
    "source_path",
    "input_path",
}


class RecordValidationError(ValueError):
    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


@dataclass(frozen=True)
class CanonicalRecord:
    row_number: int
    source_product: str
    package_version: str
    source_layer_name: str
    metadata_edition: str
    reference_year: int
    site_id: int
    source_id: int
    lc_code18: int
    class_label: str | None
    quality_status: str
    source_shape_area: float | None
    calculated_area_m2: float
    geometry_wkb: bytes
    source_attributes: dict[str, Any]
    record_fingerprint: str

    @property
    def identity(self) -> tuple[str, str, int, int]:
        return (
            self.source_product,
            self.package_version,
            self.site_id,
            self.source_id,
        )


@dataclass(frozen=True)
class RejectedRecord:
    row_number: int
    reason: str
    message: str
    source_id: Any = None
    site_id: Any = None
    lc_code18: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "row_number": self.row_number,
            "reason": self.reason,
            "message": self.message,
            "source_id": _json_safe(self.source_id),
            "site_id": _json_safe(self.site_id),
            "lc_code18": _json_safe(self.lc_code18),
        }


@dataclass(frozen=True)
class IdentityClassification:
    unique_records: tuple[CanonicalRecord, ...]
    duplicate_count: int
    conflicts: tuple[tuple[CanonicalRecord, CanonicalRecord], ...]


def sha256_file(path: str) -> str:
    digest = sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_identities(records: list[CanonicalRecord]) -> IdentityClassification:
    by_identity: dict[tuple[str, str, int, int], CanonicalRecord] = {}
    duplicates = 0
    conflicts: list[tuple[CanonicalRecord, CanonicalRecord]] = []

    for record in records:
        previous = by_identity.get(record.identity)
        if previous is None:
            by_identity[record.identity] = record
        elif previous.record_fingerprint == record.record_fingerprint:
            duplicates += 1
        else:
            conflicts.append((previous, record))

    return IdentityClassification(
        unique_records=tuple(by_identity.values()),
        duplicate_count=duplicates,
        conflicts=tuple(conflicts),
    )


def build_record(
    row_number: int,
    attributes: dict[str, Any],
    geometry: BaseGeometry,
) -> CanonicalRecord:
    source_id = _required_int(attributes, "ID")
    site_id = _required_int(attributes, "Site_id")
    lc_code18 = _required_int(attributes, "LC_code18")
    reference_year = _required_int(attributes, "Ref_year")

    if reference_year != REFERENCE_YEAR:
        raise RecordValidationError(
            "reference_year_mismatch",
            f"expected reference year {REFERENCE_YEAR}, got {reference_year}",
        )

    if not isinstance(geometry, BaseGeometry) or geometry is None:
        raise RecordValidationError("missing_geometry", "geometry is missing")
    if geometry.is_empty:
        raise RecordValidationError("empty_geometry", "geometry is empty")
    if geometry.geom_type != "MultiPolygon":
        raise RecordValidationError(
            "geometry_type",
            f"expected MultiPolygon, got {geometry.geom_type}",
        )
    if not geometry.is_valid:
        raise RecordValidationError("invalid_geometry", "geometry is not topologically valid")

    calculated_area_m2 = float(geometry.area)
    if not math.isfinite(calculated_area_m2) or calculated_area_m2 <= 0:
        raise RecordValidationError(
            "calculated_area_non_positive",
            "calculated geometry area must be finite and positive",
        )

    source_shape_area = _optional_float(attributes, "Shape_Area")
    if source_shape_area is not None:
        if not math.isfinite(source_shape_area) or source_shape_area <= 0:
            raise RecordValidationError(
                "source_area_non_positive",
                "Shape_Area must be finite and positive when present",
            )
        absolute_difference = abs(calculated_area_m2 - source_shape_area)
        tolerance = max(
            ABSOLUTE_AREA_TOLERANCE_M2,
            RELATIVE_AREA_TOLERANCE * abs(source_shape_area),
        )
        if absolute_difference > tolerance:
            raise RecordValidationError(
                "area_mismatch",
                f"area difference {absolute_difference} exceeds tolerance {tolerance}",
            )

    quality_status = "nodata" if lc_code18 == 254 else (
        "accepted" if lc_code18 in CLC_VECTOR_CLASS_LABELS else "unknown_code"
    )
    class_label = CLC_VECTOR_CLASS_LABELS.get(lc_code18)
    source_attributes = {
        str(key): _json_safe(value)
        for key, value in attributes.items()
        if str(key) != "geometry"
    }
    _ensure_json_serializable(source_attributes)

    fingerprint_attributes = {
        key: value
        for key, value in source_attributes.items()
        if key.lower() not in VOLATILE_FINGERPRINT_KEYS
    }
    fingerprint_payload = {
        "source_product": SOURCE_PRODUCT,
        "package_version": PACKAGE_VERSION,
        "source_layer_name": SOURCE_LAYER_NAME,
        "metadata_edition": METADATA_EDITION,
        "reference_year": reference_year,
        "site_id": site_id,
        "source_id": source_id,
        "lc_code18": lc_code18,
        "source_shape_area": source_shape_area,
        "attributes": fingerprint_attributes,
        "geometry_wkb_hex": to_wkb(geometry, output_dimension=2).hex(),
    }
    fingerprint_bytes = json.dumps(
        fingerprint_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")

    return CanonicalRecord(
        row_number=row_number,
        source_product=SOURCE_PRODUCT,
        package_version=PACKAGE_VERSION,
        source_layer_name=SOURCE_LAYER_NAME,
        metadata_edition=METADATA_EDITION,
        reference_year=reference_year,
        site_id=site_id,
        source_id=source_id,
        lc_code18=lc_code18,
        class_label=class_label,
        quality_status=quality_status,
        source_shape_area=source_shape_area,
        calculated_area_m2=calculated_area_m2,
        geometry_wkb=to_wkb(geometry, output_dimension=2),
        source_attributes=source_attributes,
        record_fingerprint=sha256(fingerprint_bytes).hexdigest(),
    )


def _required_int(attributes: dict[str, Any], key: str) -> int:
    value = attributes.get(key)
    if _is_missing(value) or isinstance(value, bool):
        raise RecordValidationError("missing_identifier", f"{key} is missing")
    try:
        converted = int(value)
    except (TypeError, ValueError) as exc:
        raise RecordValidationError("malformed_identifier", f"{key} is not an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise RecordValidationError("malformed_identifier", f"{key} is not an integer")
    return converted


def _optional_float(attributes: dict[str, Any], key: str) -> float | None:
    value = attributes.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise RecordValidationError("malformed_area", f"{key} is not numeric") from exc


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        result = pd.isna(value)
        return bool(result) if not hasattr(result, "__len__") else False
    except (TypeError, ValueError):
        return False


def _json_safe(value: Any) -> Any:
    if _is_missing(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    if hasattr(value, "isoformat") and not isinstance(value, (str, bytes)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, bytes):
        return value.hex()
    return value


def _ensure_json_serializable(value: Any) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RecordValidationError(
            "non_json_attribute",
            "source attributes cannot be represented as JSON",
        ) from exc
