"""Final event encoding and negotiated diagnostic policy."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from .diagnostics import (
    MAX_DESCRIPTION_BYTES,
    MAX_EVENT_BYTES,
    MAX_FRAME_LOCALS_BYTES,
    MAX_VALUE_BYTES,
    MIN_EVENT_BYTES,
    PayloadTooLarge,
    ReportingValidationError,
    bound_diagnostic,
    encode,
    legacy_projection,
    mapping,
    minimal,
    omit_values,
    prune_context,
    reduction,
    sequence,
    truncate,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Limits:
    event: int = MAX_EVENT_BYTES
    value: int = MAX_VALUE_BYTES
    frame: int = MAX_FRAME_LOCALS_BYTES

    @classmethod
    def from_environment(cls) -> Limits:
        invalid = False

        def read(name: str, default: int, minimum: int = 1) -> int:
            nonlocal invalid
            raw = os.getenv(name)
            if raw is None:
                return default
            if (
                len(raw) > 20
                or not raw.isascii()
                or not raw.isdecimal()
                or int(raw) < minimum
            ):
                invalid = True
                return default
            return int(raw)

        result = cls(
            read("TESTINEL_MAX_EVENT_BYTES", MAX_EVENT_BYTES, MIN_EVENT_BYTES),
            read("TESTINEL_MAX_VALUE_BYTES", MAX_VALUE_BYTES),
            read("TESTINEL_MAX_FRAME_LOCALS_BYTES", MAX_FRAME_LOCALS_BYTES),
        )
        if invalid:
            logger.warning(
                "Invalid Testinel byte-limit configuration; using documented defaults for invalid values."
            )
        return result


def negotiate(response: Any, limit: int) -> tuple[int, int]:
    capabilities = mapping(mapping(response).get("ingest_capabilities"))
    versions = capabilities.get("diagnostic_schema_versions")
    advertised = capabilities.get("max_event_bytes")
    if (
        not isinstance(versions, list)
        or any(type(v) is not int for v in versions)
        or type(advertised) is not int
        or advertised <= 0
    ):
        return 1, limit
    # A real but unusably low server ceiling must fail explicitly, never be ignored.
    return (2 if 2 in versions else 1), min(limit, advertised)


def bound_event(
    event: dict[str, Any], limits: Limits, schema_version: int = 1
) -> tuple[dict[str, Any], bytes]:
    """Reduce a copy. Exact bytes include caller-allocated event/request IDs."""
    result = dict(event)
    if "event" not in event:
        # Upload-link filenames are attachment identity, not optional metadata.
        data = encode(result)
        if len(data) > limits.event:
            raise PayloadTooLarge("upload_link", len(data), limits.event)
        return result, data
    payload = dict(mapping(event.get("payload")))
    if "payload" in event:
        result["payload"] = payload
    error = payload.get("error_info")
    canonical = (
        bound_diagnostic(
            error, limits.event, value_limit=limits.value, frame_limit=limits.frame
        )
        if isinstance(error, dict)
        else None
    )
    meta = reduction(event.get("reduction"))
    changed = bool(event.get("reduction"))

    def test_info(test: Any) -> dict[str, Any]:
        nonlocal changed
        data = dict(mapping(test))
        identity = data.get("test_id")
        if isinstance(identity, str) and len(identity) > 1024:
            raise ReportingValidationError(
                "Testinel reporting failed: test_id exceeds the backend 1024-character identity limit"
            )
        desc = data.get("description")
        if isinstance(desc, str):
            bounded = truncate(desc, MAX_DESCRIPTION_BYTES)
            changed |= desc != bounded
            if desc != bounded:
                meta["truncated_values"] += 1
            data["description"] = bounded
        return data

    if "tests" in event:
        result["tests"] = [test_info(t) for t in sequence(event["tests"])]
    if "test" in payload:
        payload["test"] = test_info(payload["test"])

    def bounded_json(value: Any, depth: int = 0) -> Any:
        nonlocal changed
        if isinstance(value, str):
            bounded = truncate(value, limits.value)
            if bounded != value:
                changed = True
                meta["truncated_values"] += 1
            return bounded
        if isinstance(value, (list, tuple, dict)):
            if depth >= 8:
                changed = True
                meta["metadata_omitted"] = True
                return {} if isinstance(value, dict) else []
            if len(value) > 50:
                changed = True
                meta["metadata_omitted"] = True
            if isinstance(value, dict):
                return {
                    k: bounded_json(v, depth + 1) for k, v in list(value.items())[:50]
                }
            return [bounded_json(v, depth + 1) for v in value[:50]]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        changed = True
        meta["metadata_omitted"] = True
        return None

    for key in ("options", "environment", "args"):
        if key in payload:
            payload[key] = bounded_json(payload[key])

    def body() -> bytes:
        if canonical is not None:
            payload["error_info"] = (
                canonical if schema_version == 2 else legacy_projection(canonical)
            )
        if changed:
            result["reduction"] = meta
        return encode(result)

    data = body()
    initial = len(data)
    if len(data) > limits.event and canonical is not None:
        omit_values(canonical)
        data = body()
    if len(data) > limits.event and canonical is not None:
        prune_context(canonical)
        data = body()
    if len(data) > limits.event:
        changed = True
        meta["metadata_omitted"] = True
        for test in result.get("tests", []):
            test["description"] = ""
            test["location"] = []
            for key in list(test):
                if key not in ("test_id", "description", "location"):
                    del test[key]
        if "test" in payload:
            payload["test"]["description"] = ""
        for key in ("options", "environment"):
            if key in payload:
                payload[key] = {}
        if "args" in payload:
            payload["args"] = []
        for key in list(result):
            if key not in (
                "event",
                "run_id",
                "timestamp",
                "event_id",
                "request_id",
                "sdk",
                "payload",
                "tests",
                "screenshots",
                "reduction",
            ):
                del result[key]
        for key in list(payload):
            if key not in (
                "test",
                "outcome",
                "duration",
                "error_info",
                "screenshots",
                "args",
                "options",
                "environment",
            ):
                del payload[key]
        data = body()
    if len(data) > limits.event and canonical is not None:
        minimal(canonical)
        data = body()
    if len(data) > limits.event:
        raise PayloadTooLarge(
            str(event.get("event", "upload_link")), len(data), limits.event
        )
    logger.debug(
        "Testinel payload event=%s event_id=%s request_id=%s pre_budget_bytes=%s final_bytes=%s schema=%s envelope_reduction=%s diagnostic_reduction=%s",
        event.get("event"),
        event.get("event_id"),
        event.get("request_id"),
        initial,
        len(data),
        schema_version,
        meta if changed else {},
        canonical["reduction"] if canonical else {},
    )
    return result, data
