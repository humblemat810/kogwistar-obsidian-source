"""Canonical JSON and digest helpers for the managed vault format."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from .models import JsonObject, JsonValue


def canonical_json(value: JsonValue) -> str:
    """Serialize JSON values deterministically and without executable content."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_digest(value: JsonValue, *, omit: frozenset[str] = frozenset()) -> str:
    """Return SHA-256 of a canonical object, optionally omitting top-level keys."""
    if omit:
        if not isinstance(value, Mapping):
            raise TypeError("digest omission requires an object")
        value = {str(key): item for key, item in value.items() if str(key) not in omit}
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def require_digest(payload: JsonObject, *, field: str = "digest") -> None:
    supplied = payload.get(field)
    if not isinstance(supplied, str) or supplied != canonical_digest(payload, omit=frozenset({field})):
        raise ValueError(f"invalid {field}")

