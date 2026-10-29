"""Create the isolated CLC staging table.

Revision ID: 0001_create_corine_land_cover_clc
Revises:
Create Date: 2026-10-28

"""
from alembic import op
import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_clc_staging"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.create_table(
        "corine_land_cover_clc",
        sa.Column("row_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("source_product", sa.Text(), nullable=False),
        sa.Column("package_version", sa.Text(), nullable=False),
        sa.Column("source_layer_name", sa.Text(), nullable=False),
        sa.Column("metadata_edition", sa.Text(), nullable=False),
        sa.Column("reference_year", sa.SmallInteger(), nullable=False),
        sa.Column("site_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("lc_code18", sa.SmallInteger(), nullable=False),
        sa.Column("class_label", sa.Text(), nullable=True),
        sa.Column("quality_status", sa.Text(), nullable=False),
        sa.Column("source_shape_area", sa.Double(), nullable=True),
        sa.Column("calculated_area_m2", sa.Double(), nullable=False),
        sa.Column(
            "geometry",
            geoalchemy2.Geometry(
                geometry_type="MULTIPOLYGON",
                srid=3035,
                spatial_index=False,
            ),
            nullable=False,
        ),
        sa.Column("source_attributes", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_file_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("record_fingerprint", sa.CHAR(length=64), nullable=False),
        sa.Column("loaded_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "quality_status IN ('accepted', 'nodata', 'unknown_code')",
            name="ck_corine_quality_status",
        ),
        sa.CheckConstraint(
            "calculated_area_m2 > 0",
            name="ck_corine_calculated_area_positive",
        ),
        sa.UniqueConstraint(
            "source_product",
            "package_version",
            "site_id",
            "source_id",
            name="uq_corine_clc_identity",
        ),
        schema="iris_staging",
    )
    op.create_index(
        "idx_corine_clc_geometry_gist",
        "corine_land_cover_clc",
        ["geometry"],
        unique=False,
        schema="iris_staging",
        postgresql_using="gist",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_corine_clc_geometry_gist",
        table_name="corine_land_cover_clc",
        schema="iris_staging",
    )
    op.drop_table("corine_land_cover_clc", schema="iris_staging")
