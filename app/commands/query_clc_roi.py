from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

from dotenv import load_dotenv
from sqlalchemy import create_engine

from app.clc_contract import PACKAGE_VERSION, SOURCE_PRODUCT
from app.roi import query_roi_metrics, validate_roi


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query CLC class areas for a declared-CRS ROI.")
    parser.add_argument("--roi-wkt", required=True)
    parser.add_argument("--roi-crs", required=True)
    parser.add_argument("--source-product", default=SOURCE_PRODUCT)
    parser.add_argument("--package-version", default=PACKAGE_VERSION)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--report-path", default=None)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    load_dotenv(Path(__file__).resolve().parents[2] / ".env.clc.local")
    database_url = args.database_url or os.getenv("CLC_DATABASE_URL")
    if not database_url:
        raise SystemExit("CLC_DATABASE_URL is required; it is never printed by this command")

    roi = validate_roi(args.roi_wkt, args.roi_crs)
    started = time.perf_counter()
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            result = query_roi_metrics(
                connection,
                roi,
                source_product=args.source_product,
                package_version=args.package_version,
            )
    finally:
        engine.dispose()
    result["elapsed_seconds"] = round(time.perf_counter() - started, 6)

    output = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False)
    print(output)
    if args.report_path:
        report_path = Path(args.report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(output + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
