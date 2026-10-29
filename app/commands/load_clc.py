from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from app.clc_contract import EXPECTED_FIXTURE_SHA256, EXPECTED_LAYER
from app.loaders.clc_loader import load_parse_batches
from app.parsers.clc_parser import inspect_geopackage, iter_geopackage_batches


DEFAULT_BATCH_SIZE = 20_000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load a CLC fixture into isolated staging.")
    parser.add_argument("--input", required=True, help="Input GeoPackage path")
    parser.add_argument(
        "--database-url",
        default=None,
        help="Task database URL; defaults to CLC_DATABASE_URL",
    )
    parser.add_argument(
        "--expected-sha256",
        default=EXPECTED_FIXTURE_SHA256,
        help="Expected input SHA-256",
    )
    parser.add_argument(
        "--layer",
        default=EXPECTED_LAYER,
        help="GeoPackage layer to verify and read",
    )
    parser.add_argument(
        "--report-dir",
        default="reports/clc_runs",
        help="Directory for JSON run reports",
    )
    parser.add_argument("--expected-count", type=int, default=None)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=(
            "Maximum source features read and validated at once; "
            f"default {DEFAULT_BATCH_SIZE}"
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / ".env.clc.local")
    database_url = args.database_url or os.getenv("CLC_DATABASE_URL")
    if not database_url:
        raise SystemExit("CLC_DATABASE_URL is required; it is never printed by this command")

    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be a positive integer")

    source = inspect_geopackage(
        args.input,
        expected_sha256=args.expected_sha256,
        expected_layer=args.layer,
    )
    if args.expected_count is not None and source.source_feature_count != args.expected_count:
        raise SystemExit(
            f"input feature count mismatch: expected {args.expected_count}, "
            f"got {source.source_feature_count}"
        )

    report = load_parse_batches(
        iter_geopackage_batches(source, args.batch_size),
        source_feature_count=source.source_feature_count,
        input_file=source.path.name,
        input_sha256=source.input_sha256,
        source_layer=source.source_layer,
        database_url=database_url,
        report_dir=args.report_dir,
        batch_size=args.batch_size,
    )
    output = {
        key: value
        for key, value in report.items()
        if key != "errors" or value
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if report["outcome"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
