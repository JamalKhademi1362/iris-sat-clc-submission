from __future__ import annotations

from base64 import urlsafe_b64encode
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Iterable
from urllib.parse import urlparse
from uuid import uuid4

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


CLMS_BASE_URL = "https://land.copernicus.eu"
DATASET_PAGE = (
    "https://land.copernicus.eu/en/products/clc-backbone/"
    "clc-backbone-vector-2018"
)
DATASET_UID = "9788d256594044d987828eb1e5b6e7b1"
PREPACKAGE_FILE_ID = "b9d53e04-e69f-4f62-ad75-fc39edc98bbf"
PACKAGE_IDENTIFIER = "CLMS_CLCplus_VECTOR_2018_PU131_CH-IT_03035_V1_1"
PACKAGE_VERSION = "V1_1"
PRODUCTION_UNIT = "131"
PACKAGE_COUNTRIES = "Switzerland, Italy"
ORDER_ENDPOINT = f"{CLMS_BASE_URL}/api/@datarequest_post"
STATUS_ENDPOINT = f"{CLMS_BASE_URL}/api/@datarequest_status_get"
EXPECTED_TOKEN_HOST = "land.copernicus.eu"


class ClmsFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServiceKey:
    client_id: str
    user_id: str
    token_uri: str
    private_key: str


def build_retrying_session(max_retries: int = 3) -> requests.Session:
    retry = Retry(
        total=max_retries,
        connect=max_retries,
        read=max_retries,
        status=max_retries,
        allowed_methods=frozenset({"GET"}),
        status_forcelist=(429, 500, 502, 503, 504),
        backoff_factor=0.5,
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    return session


def load_service_key(path: str | Path) -> ServiceKey:
    key_path = Path(path)
    try:
        payload = json.loads(key_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClmsFetchError("CLMS service-key file could not be read as JSON") from exc
    required = {"client_id", "user_id", "token_uri", "private_key"}
    missing = sorted(required - payload.keys())
    if missing:
        raise ClmsFetchError(f"CLMS service-key fields are missing: {missing}")
    token_uri = str(payload["token_uri"])
    parsed = urlparse(token_uri)
    if parsed.scheme != "https" or parsed.hostname != EXPECTED_TOKEN_HOST:
        raise ClmsFetchError("CLMS token_uri must use HTTPS on land.copernicus.eu")
    return ServiceKey(
        client_id=str(payload["client_id"]),
        user_id=str(payload["user_id"]),
        token_uri=token_uri,
        private_key=str(payload["private_key"]),
    )


def create_jwt_assertion(service_key: ServiceKey, now: int | None = None) -> str:
    issued_at = int(time.time() if now is None else now)
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": service_key.client_id,
        "sub": service_key.user_id,
        "aud": service_key.token_uri,
        "iat": issued_at,
        "exp": issued_at + 3600,
    }
    signing_input = b".".join(
        (
            _base64url_json(header),
            _base64url_json(claims),
        )
    )
    try:
        private_key = serialization.load_pem_private_key(
            service_key.private_key.encode("utf-8"), password=None
        )
        signature = private_key.sign(
            signing_input,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except (TypeError, ValueError) as exc:
        raise ClmsFetchError("CLMS service-key private key is invalid") from exc
    return b".".join((signing_input, _base64url(signature))).decode("ascii")


class AccessTokenProvider:
    def __init__(
        self,
        session: requests.Session,
        service_key: ServiceKey,
        timeout: tuple[float, float],
    ) -> None:
        self.session = session
        self.service_key = service_key
        self.timeout = timeout
        self._token: str | None = None

    def get(self, force_refresh: bool = False) -> str:
        if self._token is not None and not force_refresh:
            return self._token
        try:
            response = self.session.post(
                self.service_key.token_uri,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": create_jwt_assertion(self.service_key),
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ClmsFetchError("CLMS token exchange failed due to a network error") from exc
        if response.status_code != 200:
            error_code = _safe_oauth_error_code(response)
            suffix = f" ({error_code})" if error_code else ""
            raise ClmsFetchError(
                f"CLMS token exchange failed with HTTP {response.status_code}{suffix}"
            )
        try:
            token = response.json()["access_token"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ClmsFetchError("CLMS token response did not contain an access token") from exc
        if not isinstance(token, str) or not token:
            raise ClmsFetchError("CLMS token response contained an invalid access token")
        self._token = token
        return token


def fetch_official_prepackage(
    destination: str | Path,
    service_key_path: str | Path,
    report_path: str | Path,
    expected_sha256: str | None = None,
    expected_checksum_source: str | None = None,
    max_wait_seconds: float = 3600.0,
    poll_interval_seconds: float = 15.0,
    max_retries: int = 3,
    timeout: tuple[float, float] = (10.0, 120.0),
    session: requests.Session | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    if expected_sha256 and not expected_checksum_source:
        raise ClmsFetchError("expected checksum source is required with expected SHA-256")
    target = Path(destination)
    if target.exists():
        raise ClmsFetchError(f"destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    client = session or build_retrying_session(max_retries=max_retries)
    close_session = session is None
    try:
        provider = AccessTokenProvider(client, load_service_key(service_key_path), timeout)
        order_response = _authorized_request(
            client,
            provider,
            "POST",
            ORDER_ENDPOINT,
            timeout=timeout,
            json={
                "Datasets": [
                    {"DatasetID": DATASET_UID, "FileID": PREPACKAGE_FILE_ID}
                ]
            },
        )
        _require_success(order_response, "CLMS prepackage order", expected=(201,))
        task_id = _extract_task_id(order_response)
        status = _wait_for_download(
            client,
            provider,
            task_id,
            timeout=timeout,
            max_wait_seconds=max_wait_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        download_url = status.get("DownloadURL")
        if not isinstance(download_url, str) or not download_url:
            raise ClmsFetchError("completed CLMS task did not provide a download URL")
        if urlparse(download_url).scheme != "https":
            raise ClmsFetchError("CLMS download URL must use HTTPS")
        download = stream_download(
            client,
            download_url,
            target,
            timeout=timeout,
            expected_sha256=expected_sha256,
        )
        report = {
            "outcome": "success",
            "official_source": DATASET_PAGE,
            "dataset_uid": DATASET_UID,
            "prepackage_file_id": PREPACKAGE_FILE_ID,
            "package_identifier": PACKAGE_IDENTIFIER,
            "package_version": PACKAGE_VERSION,
            "production_unit": PRODUCTION_UNIT,
            "countries": PACKAGE_COUNTRIES,
            "task_id": str(task_id),
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(time.perf_counter() - started, 6),
            "file_name": target.name,
            "size_bytes": download["size_bytes"],
            "sha256": download["sha256"],
            "digest_kind": (
                "authoritative_expected_checksum_verified"
                if expected_sha256
                else "local_fingerprint_only"
            ),
            "expected_checksum_source": expected_checksum_source,
        }
        _write_report(report, report_path)
        return report
    except ClmsFetchError as exc:
        failure_report = {
            "outcome": "failed",
            "official_source": DATASET_PAGE,
            "dataset_uid": DATASET_UID,
            "prepackage_file_id": PREPACKAGE_FILE_ID,
            "package_identifier": PACKAGE_IDENTIFIER,
            "package_version": PACKAGE_VERSION,
            "production_unit": PRODUCTION_UNIT,
            "countries": PACKAGE_COUNTRIES,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(time.perf_counter() - started, 6),
            "final_file_created": target.exists(),
            "size_bytes": target.stat().st_size if target.exists() else 0,
            "sha256": None,
            "digest_kind": None,
            "error": str(exc),
        }
        _write_report(failure_report, report_path)
        raise
    finally:
        if close_session:
            client.close()


def stream_download(
    session: requests.Session,
    download_url: str,
    destination: str | Path,
    timeout: tuple[float, float] = (10.0, 120.0),
    expected_sha256: str | None = None,
    chunk_size: int = 1024 * 1024,
) -> dict[str, Any]:
    target = Path(destination)
    if target.exists():
        raise ClmsFetchError(f"destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.part")
    digest = sha256()
    size_bytes = 0
    try:
        try:
            response = session.get(download_url, stream=True, timeout=timeout)
        except requests.RequestException as exc:
            raise ClmsFetchError("CLMS package download failed due to a network error") from exc
        _require_success(response, "CLMS package download", expected=(200,))
        with temporary.open("xb") as handle:
            try:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    digest.update(chunk)
                    size_bytes += len(chunk)
            except requests.RequestException as exc:
                raise ClmsFetchError(
                    "CLMS package download was interrupted by a network error"
                ) from exc
            handle.flush()
            os.fsync(handle.fileno())
        observed = digest.hexdigest()
        if expected_sha256 and observed.lower() != expected_sha256.lower():
            raise ClmsFetchError(
                f"download SHA-256 mismatch: expected {expected_sha256}, got {observed}"
            )
        temporary.replace(target)
        return {"size_bytes": size_bytes, "sha256": observed}
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _authorized_request(
    session: requests.Session,
    provider: AccessTokenProvider,
    method: str,
    url: str,
    **kwargs: Any,
):
    for attempt in range(2):
        token = provider.get(force_refresh=attempt == 1)
        try:
            response = session.request(
                method,
                url,
                headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
                **kwargs,
            )
        except requests.RequestException as exc:
            raise ClmsFetchError("CLMS API request failed due to a network error") from exc
        if response.status_code != 401 or attempt == 1:
            return response
    raise AssertionError("unreachable")


def _wait_for_download(
    session: requests.Session,
    provider: AccessTokenProvider,
    task_id: str,
    timeout: tuple[float, float],
    max_wait_seconds: float,
    poll_interval_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + max_wait_seconds
    while True:
        response = _authorized_request(
            session,
            provider,
            "GET",
            STATUS_ENDPOINT,
            timeout=timeout,
            params={"TaskID": task_id},
        )
        _require_success(response, "CLMS task status", expected=(200,))
        try:
            payload = response.json()
        except ValueError as exc:
            raise ClmsFetchError("CLMS task status response was not JSON") from exc
        status = payload.get("Status")
        if status == "Finished_ok":
            return payload
        if status not in {"In_progress", "Queued", "Accepted", "Submitted"}:
            message = payload.get("Message") or "no server message"
            raise ClmsFetchError(f"CLMS task failed with status {status!r}: {message}")
        if time.monotonic() >= deadline:
            raise ClmsFetchError("CLMS task did not finish before the configured timeout")
        time.sleep(min(poll_interval_seconds, max(0.0, deadline - time.monotonic())))


def _extract_task_id(response: Any) -> str:
    try:
        payload = response.json()
        errors = payload.get("ErrorTaskIds", [])
        tasks = payload["TaskIds"]
        task_id = tasks[0]["TaskID"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ClmsFetchError("CLMS order response did not contain a task ID") from exc
    if errors:
        raise ClmsFetchError("CLMS order response reported failed task creation")
    return str(task_id)


def _require_success(response: Any, operation: str, expected: Iterable[int]) -> None:
    if response.status_code not in set(expected):
        raise ClmsFetchError(f"{operation} failed with HTTP {response.status_code}")


def _safe_oauth_error_code(response: Any) -> str | None:
    try:
        code = response.json().get("error")
    except (ValueError, AttributeError, TypeError):
        return None
    if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", code):
        return code
    return None


def _write_report(report: dict[str, Any], report_path: str | Path) -> None:
    destination = Path(report_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(destination)


def _base64url_json(value: dict[str, Any]) -> bytes:
    return _base64url(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _base64url(value: bytes) -> bytes:
    return urlsafe_b64encode(value).rstrip(b"=")
