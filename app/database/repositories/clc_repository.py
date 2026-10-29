from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import text

from app.clc_contract import CanonicalRecord


IDENTITY_COLUMNS = ("source_product", "package_version", "site_id", "source_id")


class ClcRepository:
    """Scoped CLC repository; transaction ownership stays with the loader."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def existing_fingerprints(
        self,
        identities: Iterable[tuple[str, str, int, int]],
    ) -> dict[tuple[str, str, int, int], str]:
        return self._fingerprints_from_table(
            identities, "iris_staging.corine_land_cover_clc"
        )

    def run_fingerprints(
        self,
        identities: Iterable[tuple[str, str, int, int]],
    ) -> dict[tuple[str, str, int, int], str]:
        return self._fingerprints_from_table(identities, "pg_temp.clc_ingest_seen")

    def remember_run_records(self, records: Iterable[CanonicalRecord]) -> None:
        parameters = [
            {
                "source_product": record.source_product,
                "package_version": record.package_version,
                "site_id": record.site_id,
                "source_id": record.source_id,
                "record_fingerprint": record.record_fingerprint,
            }
            for record in records
        ]
        if not parameters:
            return
        self.connection.execute(
            text(
                """
                INSERT INTO pg_temp.clc_ingest_seen (
                    source_product, package_version, site_id, source_id,
                    record_fingerprint
                ) VALUES (
                    :source_product, :package_version, :site_id, :source_id,
                    :record_fingerprint
                )
                """
            ),
            parameters,
        )

    def stage_batch_records(self, records: Iterable[CanonicalRecord]) -> None:
        parameters = [
            {
                "source_product": record.source_product,
                "package_version": record.package_version,
                "site_id": record.site_id,
                "source_id": record.source_id,
                "record_fingerprint": record.record_fingerprint,
            }
            for record in records
        ]
        self.connection.execute(text("TRUNCATE pg_temp.clc_ingest_batch"))
        if parameters:
            self.connection.execute(
                text(
                    """
                    INSERT INTO pg_temp.clc_ingest_batch (
                        source_product, package_version, site_id, source_id,
                        record_fingerprint
                    ) VALUES (
                        :source_product, :package_version, :site_id, :source_id,
                        :record_fingerprint
                    )
                    """
                ),
                parameters,
            )

    def staged_run_fingerprints(
        self,
    ) -> dict[tuple[str, str, int, int], str]:
        return self._staged_fingerprints("pg_temp.clc_ingest_seen")

    def staged_destination_fingerprints(
        self,
    ) -> dict[tuple[str, str, int, int], str]:
        return self._staged_fingerprints("iris_staging.corine_land_cover_clc")

    def _staged_fingerprints(
        self, table_reference: str
    ) -> dict[tuple[str, str, int, int], str]:
        if table_reference not in {
            "iris_staging.corine_land_cover_clc",
            "pg_temp.clc_ingest_seen",
        }:
            raise ValueError("unsupported CLC identity table")
        rows = self.connection.execute(
            text(
                f"""
                SELECT existing.source_product, existing.package_version,
                       existing.site_id, existing.source_id,
                       existing.record_fingerprint
                FROM {table_reference} AS existing
                JOIN pg_temp.clc_ingest_batch AS batch
                  ON batch.source_product = existing.source_product
                 AND batch.package_version = existing.package_version
                 AND batch.site_id = existing.site_id
                 AND batch.source_id = existing.source_id
                """
            )
        ).mappings()
        return {
            (
                row["source_product"],
                row["package_version"],
                row["site_id"],
                row["source_id"],
            ): row["record_fingerprint"].strip()
            for row in rows
        }

    def _fingerprints_from_table(
        self,
        identities: Iterable[tuple[str, str, int, int]],
        table_reference: str,
    ) -> dict[tuple[str, str, int, int], str]:
        if table_reference not in {
            "iris_staging.corine_land_cover_clc",
            "pg_temp.clc_ingest_seen",
        }:
            raise ValueError("unsupported CLC identity table")
        identities = list(identities)
        existing: dict[tuple[str, str, int, int], str] = {}
        for chunk_start in range(0, len(identities), 250):
            chunk = identities[chunk_start : chunk_start + 250]
            if not chunk:
                continue
            clauses: list[str] = []
            parameters: dict[str, Any] = {}
            for index, identity in enumerate(chunk):
                product, package, site_id, source_id = identity
                names = {
                    "product": f"product_{index}",
                    "package": f"package_{index}",
                    "site": f"site_{index}",
                    "source": f"source_{index}",
                }
                clauses.append(
                    "(source_product = :{product} AND package_version = :{package} "
                    "AND site_id = :{site} AND source_id = :{source})".format(**names)
                )
                parameters.update(
                    {
                        names["product"]: product,
                        names["package"]: package,
                        names["site"]: site_id,
                        names["source"]: source_id,
                    }
                )
            query = text(
                "SELECT source_product, package_version, site_id, source_id, "
                f"record_fingerprint FROM {table_reference} WHERE "
                + " OR ".join(clauses)
            )
            for row in self.connection.execute(query, parameters).mappings():
                identity = (
                    row["source_product"],
                    row["package_version"],
                    row["site_id"],
                    row["source_id"],
                )
                existing[identity] = row["record_fingerprint"].strip()
        return existing

    def insert(self, record: CanonicalRecord, run_id: str, input_sha256: str) -> bool:
        statement = self._insert_statement(returning=True)
        return self.connection.execute(
            statement,
            self._insert_parameters(record, run_id, input_sha256),
        ).first() is not None

    def insert_many(
        self,
        records: Iterable[CanonicalRecord],
        run_id: str,
        input_sha256: str,
    ) -> int:
        parameters = [
            self._insert_parameters(record, run_id, input_sha256)
            for record in records
        ]
        if not parameters:
            return 0
        result = self.connection.execute(
            self._insert_statement(returning=False), parameters
        )
        return int(result.rowcount)

    @staticmethod
    def _insert_statement(*, returning: bool):
        sql = (
            """
            INSERT INTO iris_staging.corine_land_cover_clc (
                source_product, package_version, source_layer_name,
                metadata_edition, reference_year, site_id, source_id,
                lc_code18, class_label, quality_status, source_shape_area,
                calculated_area_m2, geometry, source_attributes,
                source_file_sha256, run_id, record_fingerprint
            ) VALUES (
                :source_product, :package_version, :source_layer_name,
                :metadata_edition, :reference_year, :site_id, :source_id,
                :lc_code18, :class_label, :quality_status, :source_shape_area,
                :calculated_area_m2, ST_GeomFromWKB(:geometry_wkb, 3035),
                CAST(:source_attributes AS jsonb), :source_file_sha256,
                CAST(:run_id AS uuid), :record_fingerprint
            )
            ON CONFLICT (source_product, package_version, site_id, source_id)
            DO NOTHING
            """
        )
        if returning:
            sql += " RETURNING row_id"
        return text(sql)

    @staticmethod
    def _insert_parameters(
        record: CanonicalRecord, run_id: str, input_sha256: str
    ) -> dict[str, Any]:
        return {
            "source_product": record.source_product,
            "package_version": record.package_version,
            "source_layer_name": record.source_layer_name,
            "metadata_edition": record.metadata_edition,
            "reference_year": record.reference_year,
            "site_id": record.site_id,
            "source_id": record.source_id,
            "lc_code18": record.lc_code18,
            "class_label": record.class_label,
            "quality_status": record.quality_status,
            "source_shape_area": record.source_shape_area,
            "calculated_area_m2": record.calculated_area_m2,
            "geometry_wkb": record.geometry_wkb,
            "source_attributes": _json_text(record.source_attributes),
            "source_file_sha256": input_sha256,
            "run_id": run_id,
            "record_fingerprint": record.record_fingerprint,
        }


def _json_text(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
