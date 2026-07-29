import hashlib
import json
from typing import Any

from app.imports.errors import ImportIntegrityError, ImportValidationError

JsonObject = dict[str, Any]


def parse_json_object(raw_data: str | bytes) -> JsonObject:
    if not raw_data or (isinstance(raw_data, str) and not raw_data.strip()):
        raise ImportValidationError(
            code="empty_data", description="Import data must not be empty."
        )
    try:
        decoded = json.loads(raw_data)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ImportValidationError(
            code="invalid_json",
            description="Import data must contain valid UTF-8 JSON.",
        ) from error
    if not isinstance(decoded, dict):
        raise ImportValidationError(
            code="invalid_root",
            description="Import data must have a JSON object as its root.",
        )
    return decoded


def canonical_json(payload: JsonObject) -> bytes:
    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ImportValidationError(
            code="non_canonical_json",
            description="Import data contains unsupported canonical JSON values.",
            affected_record=payload,
        ) from error
    return serialized.encode("utf-8")


def canonical_sha256(payload: JsonObject) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def verify_hash(actual_hash: str, expected_hash: str | None) -> None:
    if expected_hash is not None and actual_hash != expected_hash.lower():
        raise ImportIntegrityError(
            code="hash_mismatch",
            description="The payload hash does not match the expected hash.",
        )
