from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MIN_AGGREGATE_COUNT = 30
PII_NAME_PATTERN = re.compile(
    r"(^|_)(name|mobile|phone|id_?card|identity|email|address|bank_?card|姓名|手机号|身份证|邮箱|地址|银行卡)($|_)",
    re.IGNORECASE,
)
SECRET_PATTERN = re.compile(
    r"((?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{12,}|api[_-]?key\s*[:=]|bearer\s+[A-Za-z0-9._-]{12,})",
    re.IGNORECASE,
)
VALUE_PII_PATTERN = re.compile(
    r"(?:\b1[3-9]\d{9}\b|\b\d{15}(?:\d{2}[0-9Xx])?\b|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})",
    re.IGNORECASE,
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def contains_sensitive_text(value: str) -> bool:
    return bool(SECRET_PATTERN.search(value))


def validate_provider_text(value: str) -> None:
    if contains_sensitive_text(value):
        raise ValueError("POSSIBLE_SECRET_FORBIDDEN")
    if VALUE_PII_PATTERN.search(value):
        raise ValueError("POSSIBLE_PII_VALUE_FORBIDDEN")
    lines = [line for line in value.splitlines() if line.strip()]
    tabular = sum(line.count(",") >= 3 or line.count("\t") >= 2 for line in lines)
    if tabular >= 2:
        raise ValueError("POSSIBLE_RAW_TABLE_FORBIDDEN")


def is_pii_column(name: str) -> bool:
    return bool(PII_NAME_PATTERN.search(name.strip()))


def _safe_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def validate_safe_evidence(payload: Any, path: str = "evidence") -> None:
    """Validate an already-sanitized SafeEvidence value.

    This function is deliberately recursive because the Provider boundary accepts
    nested JSON.  Field-name checks alone are not sufficient: a harmless-looking
    key can otherwise carry a phone number, identity number, email address, secret,
    or a pasted raw table in its value.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = str(key).lower()
            forbidden = {
                "raw",
                "raw_data",
                "raw_rows",
                "raw_records",
                "records",
                "sample_values",
                "record_values",
                "source_path",
                "stored_path",
            }
            forbidden_key = lowered in forbidden or lowered.startswith("raw_")
            if is_pii_column(lowered) or (forbidden_key and value is not False):
                raise ValueError(f"RAW_OR_PII_FIELD_FORBIDDEN: {path}.{key}")
            validate_safe_evidence(value, f"{path}.{key}")
        return
    if isinstance(payload, list):
        for index, value in enumerate(payload):
            validate_safe_evidence(value, f"{path}[{index}]")
        return
    if not _safe_scalar(payload):
        raise ValueError(f"UNSAFE_EVIDENCE_TYPE: {path}")
    if isinstance(payload, str):
        try:
            validate_provider_text(payload)
        except ValueError as exc:
            raise ValueError(f"{exc}: {path}") from exc


def sanitize_safe_evidence(payload: Any, path: str = "evidence") -> Any:
    """Return the only representation that may cross the Provider gateway.

    Aggregate list rows with a conventional ``count``/``n``/``sample_count``
    field are suppressed below the product-wide threshold.  Validation is run on
    the transformed value, so callers cannot accidentally validate one object and
    transmit another.
    """
    if isinstance(payload, dict):
        result = {
            str(key): sanitize_safe_evidence(value, f"{path}.{key}")
            for key, value in payload.items()
        }
        count_key = next(
            (key for key in ("count", "sample_count", "n") if key in result),
            None,
        )
        if count_key is not None:
            count = result[count_key]
            if isinstance(count, (int, float)) and not isinstance(count, bool):
                if int(count) < MIN_AGGREGATE_COUNT:
                    result = {count_key: int(count), "suppressed": True}
        validate_safe_evidence(result, path)
        return result
    if isinstance(payload, list):
        result = [
            sanitize_safe_evidence(value, f"{path}[{index}]") for index, value in enumerate(payload)
        ]
        validate_safe_evidence(result, path)
        return result
    validate_safe_evidence(payload, path)
    return payload


def suppress_small_groups(
    rows: list[dict[str, Any]], count_key: str = "count"
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        count = int(row.get(count_key) or 0)
        if count < MIN_AGGREGATE_COUNT:
            result.append({count_key: count, "suppressed": True})
        else:
            result.append(dict(row))
    return result


@dataclass(frozen=True)
class EncryptedEnvelope:
    salt: str
    nonce: str
    ciphertext: str
    kdf: str = "scrypt-n16384-r8-p1"
    cipher: str = "AES-256-GCM"

    def dumps(self) -> bytes:
        return json.dumps(self.__dict__, sort_keys=True).encode("utf-8")


def derive_key(password: str, salt: bytes) -> bytes:
    if len(password) < 10:
        raise ValueError("ARCHIVE_PASSWORD_TOO_SHORT")
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)


def generate_recovery_key() -> str:
    return "RMA-" + secrets.token_urlsafe(32)


def encrypt_bytes(
    value: bytes, password: str, associated_data: bytes = b"risk-model-agent-v1"
) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise RuntimeError("CRYPTOGRAPHY_DEPENDENCY_REQUIRED") from exc
    salt = os.urandom(16)
    nonce = os.urandom(12)
    encrypted = AESGCM(derive_key(password, salt)).encrypt(nonce, value, associated_data)
    envelope = EncryptedEnvelope(salt.hex(), nonce.hex(), encrypted.hex())
    return envelope.dumps()


def decrypt_bytes(
    value: bytes, password: str, associated_data: bytes = b"risk-model-agent-v1"
) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise RuntimeError("CRYPTOGRAPHY_DEPENDENCY_REQUIRED") from exc
    try:
        payload = json.loads(value.decode("utf-8"))
        salt = bytes.fromhex(payload["salt"])
        nonce = bytes.fromhex(payload["nonce"])
        ciphertext = bytes.fromhex(payload["ciphertext"])
        return AESGCM(derive_key(password, salt)).decrypt(nonce, ciphertext, associated_data)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("INVALID_ARCHIVE_OR_PASSWORD") from exc


def encrypt_file_payload(
    source: Path,
    destination: Path,
    password: str,
    associated_data: bytes = b"risk-model-agent-project-v1",
) -> tuple[dict[str, Any], str]:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise RuntimeError("CRYPTOGRAPHY_DEPENDENCY_REQUIRED") from exc
    password_salt = os.urandom(16)
    recovery_salt = os.urandom(16)
    data_key = os.urandom(32)
    payload_nonce = os.urandom(12)
    recovery_key = generate_recovery_key()
    password_nonce = os.urandom(12)
    recovery_nonce = os.urandom(12)
    key_aad = associated_data + b"-data-key"
    wrapped_password = AESGCM(derive_key(password, password_salt)).encrypt(
        password_nonce, data_key, key_aad
    )
    wrapped_recovery = AESGCM(derive_key(recovery_key, recovery_salt)).encrypt(
        recovery_nonce, data_key, key_aad
    )
    encryptor = Cipher(algorithms.AES(data_key), modes.GCM(payload_nonce)).encryptor()
    encryptor.authenticate_additional_data(associated_data)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, destination.open("wb") as writer:
        while chunk := reader.read(4 * 1024 * 1024):
            writer.write(encryptor.update(chunk))
        writer.write(encryptor.finalize())
    manifest = {
        "schema_version": "risk-encrypted-archive/v1",
        "cipher": "AES-256-GCM",
        "kdf": "scrypt-n16384-r8-p1",
        "payload_nonce": _b64(payload_nonce),
        "payload_tag": _b64(encryptor.tag),
        "associated_data": associated_data.decode("ascii"),
        "password_wrap": {
            "salt": _b64(password_salt),
            "nonce": _b64(password_nonce),
            "wrapped_key": _b64(wrapped_password),
        },
        "recovery_wrap": {
            "salt": _b64(recovery_salt),
            "nonce": _b64(recovery_nonce),
            "wrapped_key": _b64(wrapped_recovery),
        },
        "plaintext_sha256": sha256_file(source),
        "ciphertext_sha256": sha256_file(destination),
    }
    return manifest, recovery_key


def decrypt_file_payload(
    source: Path,
    destination: Path,
    manifest: dict[str, Any],
    credential: str,
) -> Path:
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - dependency contract
        raise RuntimeError("CRYPTOGRAPHY_DEPENDENCY_REQUIRED") from exc
    if sha256_file(source) != manifest.get("ciphertext_sha256"):
        raise ValueError("ARCHIVE_CIPHERTEXT_CHECKSUM_MISMATCH")
    associated_data = str(manifest["associated_data"]).encode("ascii")
    # ``RMA-`` is a display convention, not an authenticated credential type.
    # A perfectly valid user password may start with that prefix, so try both
    # independently wrapped data keys and disclose only one generic failure.
    wrap_names = (
        ("recovery_wrap", "password_wrap")
        if credential.startswith("RMA-")
        else ("password_wrap", "recovery_wrap")
    )
    data_key: bytes | None = None
    for wrap_name in wrap_names:
        try:
            wrap = manifest[wrap_name]
            wrapping_key = derive_key(credential, _unb64(wrap["salt"]))
            data_key = AESGCM(wrapping_key).decrypt(
                _unb64(wrap["nonce"]),
                _unb64(wrap["wrapped_key"]),
                associated_data + b"-data-key",
            )
            break
        except (InvalidTag, KeyError, ValueError, TypeError):
            continue
    if data_key is None:
        raise ValueError("INVALID_ARCHIVE_OR_CREDENTIAL")
    try:
        decryptor = Cipher(
            algorithms.AES(data_key),
            modes.GCM(_unb64(manifest["payload_nonce"]), _unb64(manifest["payload_tag"])),
        ).decryptor()
        decryptor.authenticate_additional_data(associated_data)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as reader, destination.open("wb") as writer:
            while chunk := reader.read(4 * 1024 * 1024):
                writer.write(decryptor.update(chunk))
            writer.write(decryptor.finalize())
    except (InvalidTag, KeyError, ValueError, TypeError) as exc:
        destination.unlink(missing_ok=True)
        raise ValueError("INVALID_ARCHIVE_OR_CREDENTIAL") from exc
    if sha256_file(destination) != manifest.get("plaintext_sha256"):
        destination.unlink(missing_ok=True)
        raise ValueError("ARCHIVE_PLAINTEXT_CHECKSUM_MISMATCH")
    return destination


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii"))
