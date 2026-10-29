from __future__ import annotations

from hashlib import sha256
from base64 import urlsafe_b64decode
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric import rsa
import pytest
import requests

from app.fetchers.clms_fetcher import (
    AccessTokenProvider,
    ClmsFetchError,
    ServiceKey,
    create_jwt_assertion,
    fetch_official_prepackage,
    stream_download,
)


class FakeResponse:
    def __init__(self, status_code=200, json_value=None, chunks=(), stream_error=None):
        self.status_code = status_code
        self._json_value = json_value
        self._chunks = tuple(chunks)
        self._stream_error = stream_error

    def json(self):
        return self._json_value

    def iter_content(self, chunk_size):
        for chunk in self._chunks:
            yield chunk
        if self._stream_error:
            raise self._stream_error


class DownloadSession:
    def __init__(self, response):
        self.response = response

    def get(self, url, stream, timeout):
        return self.response


class SuccessfulFetchSession:
    def post(self, url, headers, data, timeout):
        return FakeResponse(200, {"access_token": "secret-access-token"})

    def request(self, method, url, headers, **kwargs):
        if method == "POST":
            return FakeResponse(
                201,
                {"ErrorTaskIds": [], "TaskIds": [{"TaskID": "12345"}]},
            )
        return FakeResponse(
            200,
            {
                "Status": "Finished_ok",
                "DownloadURL": "https://downloads.example.test/signed-secret-url",
            },
        )

    def get(self, url, stream, timeout):
        return FakeResponse(200, chunks=(b"official-", b"package"))


class FailedTokenSession:
    def post(self, url, headers, data, timeout):
        return FakeResponse(
            400,
            {
                "error": "invalid_grant",
                "error_description": "sensitive server detail must not be exposed",
            },
        )


class InspectingTokenSession:
    def __init__(self):
        self.call = None

    def post(self, url, headers, data, timeout):
        self.call = {
            "url": url,
            "headers": headers,
            "data": data,
            "timeout": timeout,
        }
        return FakeResponse(200, {"access_token": "test-access-token"})


def _service_key_file(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    path = tmp_path / "service-key.json"
    path.write_text(
        json.dumps(
            {
                "client_id": "test-client",
                "user_id": "test-user",
                "token_uri": "https://land.copernicus.eu/@@oauth2-token",
                "private_key": private_key,
            }
        ),
        encoding="utf-8",
    )
    return path


def _decode_segment(segment):
    return urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def test_jwt_matches_documented_claims_and_has_valid_rs256_signature():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    service_key = ServiceKey(
        client_id="client-id",
        user_id="user-id",
        token_uri="https://land.copernicus.eu/@@oauth2-token",
        private_key=private_key,
    )
    assertion = create_jwt_assertion(service_key, now=1_000_000)
    header_segment, claims_segment, signature_segment = assertion.split(".")
    header = json.loads(_decode_segment(header_segment))
    claims = json.loads(_decode_segment(claims_segment))
    assert header == {"alg": "RS256", "typ": "JWT"}
    assert claims == {
        "iss": "client-id",
        "sub": "user-id",
        "aud": "https://land.copernicus.eu/@@oauth2-token",
        "iat": 1_000_000,
        "exp": 1_003_600,
    }
    key.public_key().verify(
        _decode_segment(signature_segment),
        f"{header_segment}.{claims_segment}".encode("ascii"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_successful_official_flow_streams_and_redacts_sensitive_urls(tmp_path):
    destination = tmp_path / "package.zip"
    report_path = tmp_path / "report.json"
    report = fetch_official_prepackage(
        destination=destination,
        service_key_path=_service_key_file(tmp_path),
        report_path=report_path,
        max_wait_seconds=0,
        poll_interval_seconds=0,
        session=SuccessfulFetchSession(),
    )
    assert destination.read_bytes() == b"official-package"
    assert report["sha256"] == sha256(b"official-package").hexdigest()
    assert report["digest_kind"] == "local_fingerprint_only"
    assert report["elapsed_seconds"] >= 0
    report_text = report_path.read_text(encoding="utf-8")
    assert "secret-access-token" not in report_text
    assert "signed-secret-url" not in report_text


def test_partial_network_failure_leaves_no_final_or_partial_file(tmp_path):
    destination = tmp_path / "package.zip"
    response = FakeResponse(
        200,
        chunks=(b"partial",),
        stream_error=requests.ConnectionError("connection dropped at signed URL"),
    )
    with pytest.raises(ClmsFetchError, match="interrupted by a network error") as error:
        stream_download(DownloadSession(response), "https://secret.example/file", destination)
    assert "secret.example" not in str(error.value)
    assert not destination.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_download_error_response_is_explicit_and_writes_nothing(tmp_path):
    destination = tmp_path / "package.zip"
    with pytest.raises(ClmsFetchError, match="HTTP 403"):
        stream_download(
            DownloadSession(FakeResponse(403)),
            "https://secret.example/file",
            destination,
        )
    assert not destination.exists()


def test_checksum_mismatch_removes_partial_and_final_file(tmp_path):
    destination = tmp_path / "package.zip"
    with pytest.raises(ClmsFetchError, match="SHA-256 mismatch"):
        stream_download(
            DownloadSession(FakeResponse(200, chunks=(b"content",))),
            "https://secret.example/file",
            destination,
            expected_sha256="0" * 64,
        )
    assert not destination.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_token_failure_exposes_only_sanitized_oauth_error_code(monkeypatch):
    monkeypatch.setattr(
        "app.fetchers.clms_fetcher.create_jwt_assertion", lambda service_key: "redacted"
    )
    provider = AccessTokenProvider(
        FailedTokenSession(),
        ServiceKey(
            client_id="client",
            user_id="user",
            token_uri="https://land.copernicus.eu/@@oauth2-token",
            private_key="not-used",
        ),
        (1.0, 1.0),
    )
    with pytest.raises(ClmsFetchError, match="HTTP 400 \\(invalid_grant\\)") as error:
        provider.get()
    assert "sensitive server detail" not in str(error.value)


def test_token_exchange_matches_official_form_request_and_has_no_authorization(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.fetchers.clms_fetcher.create_jwt_assertion", lambda service_key: "jwt-value"
    )
    session = InspectingTokenSession()
    provider = AccessTokenProvider(
        session,
        ServiceKey(
            client_id="client",
            user_id="user",
            token_uri="https://land.copernicus.eu/@@oauth2-token",
            private_key="not-used",
        ),
        (10.0, 120.0),
    )

    assert provider.get() == "test-access-token"
    assert session.call == {
        "url": "https://land.copernicus.eu/@@oauth2-token",
        "headers": {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        "data": {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": "jwt-value",
        },
        "timeout": (10.0, 120.0),
    }
    assert "Authorization" not in session.call["headers"]


def test_failed_official_flow_writes_sanitized_audit_report(tmp_path):
    destination = tmp_path / "package.zip"
    report_path = tmp_path / "failed-report.json"
    with pytest.raises(ClmsFetchError, match="invalid_grant"):
        fetch_official_prepackage(
            destination=destination,
            service_key_path=_service_key_file(tmp_path),
            report_path=report_path,
            session=FailedTokenSession(),
        )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["outcome"] == "failed"
    assert report["final_file_created"] is False
    assert report["size_bytes"] == 0
    assert report["sha256"] is None
    assert report["error"].endswith("(invalid_grant)")
    assert "sensitive server detail" not in report_path.read_text(encoding="utf-8")
