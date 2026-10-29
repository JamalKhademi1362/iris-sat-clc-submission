from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from app.fetchers.clms_fetcher import ClmsFetchError, fetch_official_prepackage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Order and download the official CLMS CLCplus PU131 V1_1 prepackage."
    )
    parser.add_argument("--destination", required=True)
    parser.add_argument("--report-path", required=True)
    parser.add_argument("--service-key-file", default=None)
    parser.add_argument("--expected-sha256", default=None)
    parser.add_argument("--expected-checksum-source", default=None)
    parser.add_argument("--max-wait-seconds", type=float, default=3600.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=15.0)
    parser.add_argument("--max-retries", type=int, default=3)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env.clms.local")
    service_key_file = args.service_key_file or os.getenv("CLMS_SERVICE_KEY_FILE")
    if not service_key_file:
        raise SystemExit(
            "CLMS_SERVICE_KEY_FILE is required; create an API token through the "
            "CLMS account API Tokens page and store its JSON outside version control"
        )
    try:
        report = fetch_official_prepackage(
            destination=args.destination,
            service_key_path=service_key_file,
            report_path=args.report_path,
            expected_sha256=args.expected_sha256,
            expected_checksum_source=args.expected_checksum_source,
            max_wait_seconds=args.max_wait_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            max_retries=args.max_retries,
        )
    except ClmsFetchError as exc:
        raise SystemExit(f"official CLMS fetch failed: {exc}") from None
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
