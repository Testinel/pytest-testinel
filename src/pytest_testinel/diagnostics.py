"""Bounded diagnostic protocol, policy 1.

Vendored in Testinel-web's diag/diagnostic_payload.py; keep the policy and golden
fixtures synchronized. No runtime dependency between producer and reader.
The pytest-testinel 0.10.0 / web 0.20.0 v1 projection remains supported indefinitely.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, TypedDict

MAX_EVENT_BYTES = 786432
MIN_EVENT_BYTES = 16384
MAX_VALUE_BYTES = 4096
MAX_FRAME_LOCALS_BYTES = 16384
MAX_MESSAGE_BYTES = 16384
MAX_DETAILS_BYTES = 32768
MAX_SOURCE_BYTES = 16384
MAX_DESCRIPTION_BYTES = 4096
MAX_FRAMES = 128
MAX_CAUSES = 16
MAX_ENTRIES = 50
MARKER = "…[truncated]…"


class ExceptionInfo(TypedDict):
    type: str
    message: str
    notes: list[str]


class RenderedValue(TypedDict):
    name: str | None
    value: str


class Frame(TypedDict):
    path: str | None
    line: int | None
    function: str | None
    start_line: int | None
    source: list[str]
    args: list[RenderedValue]
    locals: list[RenderedValue]


class Cause(TypedDict):
    exception: ExceptionInfo | None
    frames: list[Frame]
    relationship: str


class Diagnostic(TypedDict):
    schema_version: int
    exception: ExceptionInfo | None
    frames: list[Frame]
    causes: list[Cause]
    details: list[str]
    text: str | None
    captures: list[list[str]]
    reduction: dict[str, Any]


class ReportingValidationError(ValueError):
    """Content-free reporting failure safe to display to an operator."""


class PayloadTooLarge(ReportingValidationError):
    def __init__(self, kind: str, size: int, limit: int) -> None:
        self.kind, self.size, self.limit = kind, size, limit
        super().__init__(
            f"Testinel reporting failed: event={kind} reason=irreducible_payload bytes={size} limit={limit}"
        )


def encode(value: Any) -> bytes:
    # This is also the authoritative HTTP encoder, including JSON escaping.
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def truncate(value: str, limit: int) -> str:
    raw = value.encode("utf-8", errors="replace")
    if len(raw) <= limit:
        return value
    marker = MARKER.encode("utf-8")
    if limit < len(marker):
        return "[truncated]"[:limit]
    remaining = max(0, limit - len(marker))
    head = remaining // 2
    tail = remaining - head
    return (
        raw[:head].decode("utf-8", "ignore")
        + MARKER
        + (raw[-tail:].decode("utf-8", "ignore") if tail else "")
    )


def mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def sequence(value: Any) -> list[Any] | tuple[Any, ...]:
    return value if isinstance(value, (list, tuple)) else []


def reduction(value: Any) -> dict[str, Any]:
    data = mapping(value)
    result: dict[str, Any] = {"policy_version": 1}
    for key in (
        "truncated_values",
        "omitted_frames",
        "omitted_causes",
        "omitted_entries",
    ):
        v = data.get(key, 0)
        result[key] = min(v, 2**31 - 1) if type(v) is int and v >= 0 else 0
    for key in (
        "locals_omitted",
        "arguments_omitted",
        "details_omitted",
        "metadata_omitted",
    ):
        result[key] = data.get(key) is True
    return result


def normalize(
    error: Mapping[str, Any],
    *,
    value_limit: int = MAX_VALUE_BYTES,
    frame_limit: int = MAX_FRAME_LOCALS_BYTES,
) -> Diagnostic:
    """Pure, idempotent v1/v2 adapter; absent version means legacy v1.

    Deprecated since pytest-testinel 0.10.0, repr_info/traceback snapshots are
    accepted indefinitely by web 0.20.0 and projected only for compatibility.
    """
    version = error.get("schema_version", 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError("Unsupported diagnostic schema_version")
    meta = reduction(error.get("reduction"))
    detailed_frames = 0

    def bounded(value: Any, limit: int) -> str:
        text = value if isinstance(value, str) else ""
        result = truncate(text, limit)
        if result != text:
            meta["truncated_values"] += 1
        elif MARKER in text:
            meta["truncated_values"] = max(1, meta["truncated_values"])
        return result

    def texts(value: Any, limit: int) -> list[str]:
        result: list[str] = []
        remaining = limit
        for text in sequence(value):
            if not isinstance(text, str):
                continue
            if remaining <= len(MARKER.encode()):
                meta["truncated_values"] += 1
                break
            part = bounded(text, remaining)
            result.append(part)
            remaining -= len(part.encode("utf-8", "replace"))
        return result

    def exception(value: Any) -> ExceptionInfo | None:
        data = mapping(value)
        if not isinstance(data.get("type"), str) or not data["type"]:
            return None
        return {
            "type": data["type"],
            "message": bounded(data.get("message"), MAX_MESSAGE_BYTES),
            "notes": texts(data.get("notes"), MAX_MESSAGE_BYTES),
        }

    def values(value: Any, *, legacy: bool = False) -> list[RenderedValue]:
        items: list[RenderedValue] = []
        for item in sequence(value):
            if legacy and isinstance(item, str):
                match = re.match(r"^([A-Za-z_]\w*)\s+= (.*)$", item, re.DOTALL)
                if match:
                    items.append(
                        {"name": match[1], "value": bounded(match[2], value_limit)}
                    )
                elif items and item.startswith((" ", "\n")):
                    # reprlocals lines can be continuation blocks, not assignments.
                    items[-1]["value"] = bounded(
                        items[-1]["value"] + "\n" + item, value_limit
                    )
                else:
                    items.append({"name": None, "value": bounded(item, value_limit)})
            else:
                if isinstance(item, (tuple, list)) and len(item) == 2:
                    name, val = item
                elif isinstance(item, dict):
                    name, val = item.get("name"), item.get("value")
                else:
                    continue
                items.append(
                    {
                        "name": bounded(name, value_limit)
                        if isinstance(name, str)
                        else None,
                        "value": bounded(val, value_limit),
                    }
                )
            if len(items) >= MAX_ENTRIES:
                if len(sequence(value)) > MAX_ENTRIES:
                    meta["omitted_entries"] += len(sequence(value)) - MAX_ENTRIES
                break
        return items

    def frames(value: Any, *, legacy: bool = False, light: Any = None) -> list[Frame]:
        nonlocal detailed_frames
        raw = sequence(value)
        output: list[Frame] = []
        # Retain a bounded location skeleton, including last user and deepest.
        indices = list(range(min(len(raw), 512)))
        for i in (
            len(raw) - 1,
            next(
                (
                    i
                    for i in range(len(raw) - 1, -1, -1)
                    if "site-package"
                    not in str(
                        mapping(
                            mapping(raw[i]).get("reprfileloc") if legacy else raw[i]
                        ).get("path")
                        or ""
                    )
                ),
                -1,
            ),
        ):
            if i >= 0 and i not in indices:
                indices.append(i)
        indices.sort()
        meta["omitted_frames"] += len(raw) - len(indices)
        light_frames = sequence(light)
        for i in indices:
            entry = mapping(raw[i])
            loc = mapping(entry.get("reprfileloc")) if legacy else entry
            path = loc.get("path")
            line = loc.get("lineno") if legacy else loc.get("line")
            lines = (
                sequence(entry.get("lines"))
                if legacy
                else sequence(entry.get("source"))
            )
            source: list[str] = []
            if legacy:
                for text in lines:
                    if not isinstance(text, str) or re.match(r"^E(?:\s|$)", text):
                        break
                    source.append(text)
            else:
                source = [text for text in lines if isinstance(text, str)]
            start = (
                line
                - next(
                    (n for n, s in enumerate(source) if s.startswith(">")),
                    max(0, len(source) - 1),
                )
                if type(line) is int
                else None
            )
            frame: Frame = {
                "path": path if isinstance(path, str) else None,
                "line": line if type(line) is int else None,
                "function": entry.get("function")
                if isinstance(entry.get("function"), str)
                else None,
                "start_line": start
                if legacy
                else (
                    entry.get("start_line")
                    if type(entry.get("start_line")) is int
                    else None
                ),
                "source": texts(source, MAX_SOURCE_BYTES),
                "args": values(
                    mapping(entry.get("reprfuncargs")).get("args")
                    if legacy
                    else entry.get("args")
                ),
                "locals": values(
                    mapping(entry.get("reprlocals")).get("lines")
                    if legacy
                    else entry.get("locals"),
                    legacy=legacy,
                ),
            }
            for info in light_frames:
                info = mapping(info)
                if (
                    info.get("filename") == path
                    or (
                        isinstance(path, str)
                        and str(info.get("filename", "")).endswith("/" + path)
                    )
                ) and info.get("lineno") == line:
                    frame["function"] = info.get("name")
            frame["args"] = [arg for arg in frame["args"] if arg not in frame["locals"]]
            while len(encode([frame["locals"], frame["args"]])) > frame_limit:
                entries = frame["locals"] or frame["args"]
                if not entries:
                    break
                entries.pop()
                meta["omitted_entries"] += 1
            if frame["source"] or frame["args"] or frame["locals"]:
                if detailed_frames >= MAX_FRAMES:
                    meta["omitted_frames"] += 1
                    frame["source"], frame["args"], frame["locals"] = [], [], []
                else:
                    detailed_frames += 1
            output.append(frame)
        return output

    repr_info = mapping(error.get("repr_info"))
    newest = exception(error.get("exception"))
    causes: list[Cause] = []
    if version == 2:
        main = frames(error.get("frames"))
        for cause in sequence(error.get("causes"))[-MAX_CAUSES:]:
            cause = mapping(cause)
            causes.append(
                {
                    "exception": exception(cause.get("exception")),
                    "frames": frames(cause.get("frames")),
                    "relationship": "direct_cause"
                    if cause.get("relationship") == "direct_cause"
                    else "context",
                }
            )
        meta["omitted_causes"] += max(
            0, len(sequence(error.get("causes"))) - MAX_CAUSES
        )
        details = texts(error.get("details"), MAX_DETAILS_BYTES)
        text = (
            bounded(error["text"], MAX_DETAILS_BYTES)
            if isinstance(error.get("text"), str)
            else None
        )
        captures = error.get("captures")
    else:
        tb = mapping(repr_info.get("reprtraceback"))
        chain = sequence(repr_info.get("chain"))
        # Only remove the actual newest mirror. Equal messages do not identify causes.
        earlier = chain
        if chain and len(sequence(chain[-1])) >= 2:
            last = sequence(chain[-1])
            if last[0] == tb and last[1] == repr_info.get("reprcrash"):
                earlier = chain[:-1]
            elif not tb:
                tb = mapping(last[0])
                earlier = chain[:-1]
        main = frames(tb.get("reprentries"), legacy=True, light=error.get("traceback"))
        if not main:
            main = frames(
                [
                    {
                        "path": mapping(f).get("filename"),
                        "line": mapping(f).get("lineno"),
                        "function": mapping(f).get("name"),
                        "source": [mapping(f).get("line")],
                    }
                    for f in sequence(error.get("traceback"))
                ]
            )
        for link in earlier[-MAX_CAUSES:]:
            link = sequence(link)
            if len(link) < 2:
                continue
            crash = mapping(link[1])
            message = crash.get("message")
            message = message if isinstance(message, str) else ""
            name, _, content = message.partition(": ")
            cause_exc = (
                exception({"type": name, "message": content or message})
                if name
                else None
            )
            causes.append(
                {
                    "exception": cause_exc,
                    "frames": frames(mapping(link[0]).get("reprentries"), legacy=True),
                    "relationship": "direct_cause"
                    if len(link) > 2 and "direct cause" in str(link[2])
                    else "context",
                }
            )
        meta["omitted_causes"] += max(0, len(earlier) - MAX_CAUSES)
        details = texts(
            [
                s
                for e in sequence(tb.get("reprentries"))
                for s in sequence(mapping(e).get("lines"))
                if isinstance(s, str)
                and s.startswith("E")
                and (
                    not newest
                    or s[1:].strip()
                    not in (
                        newest["message"],
                        f"{newest['type'].rsplit('.', 1)[-1]}: {newest['message']}",
                    )
                )
            ],
            MAX_DETAILS_BYTES,
        )
        text = (
            bounded(repr_info["message"], MAX_DETAILS_BYTES)
            if isinstance(repr_info.get("message"), str)
            else None
        )
        if newest and newest["type"] in (
            "builtins.ExceptionGroup",
            "builtins.BaseExceptionGroup",
        ):
            text = "\n".join(
                texts(
                    [
                        s
                        for e in sequence(tb.get("reprentries"))
                        for s in sequence(mapping(e).get("lines"))
                    ],
                    MAX_DETAILS_BYTES,
                )
            )
            # Preserve web 0.19.0 grouping for historical opaque ExceptionGroups.
            main = []
        if main and all(f["path"] is None for f in main):
            opaque = [
                s
                for e in sequence(tb.get("reprentries"))
                for s in sequence(mapping(e).get("lines"))
            ]
            text = "\n".join(texts(opaque, MAX_DETAILS_BYTES))
            main = frames(
                [
                    {
                        "path": mapping(f).get("filename"),
                        "line": mapping(f).get("lineno"),
                        "function": mapping(f).get("name"),
                        "source": [mapping(f).get("line")],
                    }
                    for f in sequence(error.get("traceback"))
                ]
            )
        if newest is None:
            crash_message = mapping(repr_info.get("reprcrash")).get("message")
            if not crash_message and chain:
                last = sequence(chain[-1])
                crash_message = (
                    mapping(last[1]).get("message") if len(last) > 1 else None
                )
            if isinstance(crash_message, str) and crash_message:
                text = bounded(
                    (text + "\n" if text else "") + crash_message, MAX_DETAILS_BYTES
                )
        captures = repr_info.get("sections")
    capture_out: list[list[str]] = []
    remaining = MAX_DETAILS_BYTES
    for section in sequence(captures)[:50]:
        parts = texts(sequence(section)[:2], remaining)
        if not parts:
            break
        capture_out.append(parts)
        remaining -= sum(len(p.encode("utf-8")) for p in parts)
    result: Diagnostic = {
        "schema_version": 2,
        "exception": newest,
        "frames": main,
        "causes": causes,
        "details": details,
        "text": text,
        "captures": capture_out,
        "reduction": meta,
    }
    if meta["locals_omitted"] or meta["arguments_omitted"]:
        omit_values(result)
    return result


def all_frames(error: Diagnostic) -> list[Frame]:
    return error["frames"] + [f for c in error["causes"] for f in c["frames"]]


def omit_values(error: Diagnostic) -> None:
    for frame in all_frames(error):
        frame["locals"], frame["args"] = [], []
        if frame["path"] is None or frame["line"] is None:
            frame["source"] = []
    # Pytest 7–9 strict XPASS strings contain no rendered local dumps.
    text = error["text"]
    error["text"] = (
        truncate(text, 1024) if text and text.startswith("[XPASS(strict)]") else None
    )
    error["details"], error["captures"] = [], []
    error["reduction"].update(locals_omitted=True, arguments_omitted=True)


def prune_context(error: Diagnostic) -> None:
    omit_values(error)
    for frame in all_frames(error):
        frame["source"] = []
    error["reduction"]["details_omitted"] = True


def minimal(error: Diagnostic) -> None:
    prune_context(error)
    frames = error["frames"]
    keep = {
        len(frames) - 1,
        next(
            (
                i
                for i in range(len(frames) - 1, -1, -1)
                if "site-package" not in (frames[i]["path"] or "")
            ),
            -1,
        ),
    }
    error["frames"] = [f for i, f in enumerate(frames) if i in keep]
    error["reduction"]["omitted_frames"] += len(frames) - len(error["frames"])
    error["reduction"]["omitted_causes"] += len(error["causes"])
    error["causes"] = []
    if error["exception"]:
        error["exception"]["message"] = truncate(error["exception"]["message"], 1024)
        error["exception"]["notes"] = []


def bound_diagnostic(
    error: Mapping[str, Any], limit: int = MAX_EVENT_BYTES, **kwargs: Any
) -> Diagnostic:
    result = normalize(error, **kwargs)
    for step in (omit_values, prune_context, minimal):
        if len(encode(result)) <= limit:
            return result
        step(result)
    if len(encode(result)) > limit:
        raise PayloadTooLarge("diagnostic", len(encode(result)), limit)
    return result


def legacy_projection(error: Diagnostic) -> dict[str, Any]:
    """Bounded API v1 / old-web view; deprecated since producer 0.10.0.

    Web 0.20.0 keeps v1 input/read compatibility indefinitely. No v1 mirror is
    persisted beside v2. Reconstruct newest chain only for chained exceptions.
    """

    def traceback(frames: list[Frame]) -> dict[str, Any]:
        return {
            "reprentries": [
                {
                    "lines": f["source"],
                    "reprfileloc": {
                        "path": f["path"],
                        "lineno": f["line"],
                        "message": "",
                    },
                    "reprlocals": {
                        "lines": [
                            f"{v['name']} = {v['value']}" if v["name"] else v["value"]
                            for v in f["locals"]
                        ]
                    },
                    "reprfuncargs": {
                        "args": [[v["name"], v["value"]] for v in f["args"]]
                    },
                }
                for f in frames
            ],
            "extraline": None,
            "style": "long",
        }

    def crash(exc: ExceptionInfo | None, frames: list[Frame]) -> dict[str, Any]:
        return {
            "path": frames[-1]["path"] if frames else None,
            "lineno": frames[-1]["line"] if frames else None,
            "message": f"{exc['type']}: {exc['message']}" if exc else "",
        }

    tb = traceback(error["frames"])
    cr = crash(error["exception"], error["frames"])
    if tb["reprentries"]:
        tb["reprentries"][-1]["lines"] = (
            tb["reprentries"][-1]["lines"] + error["details"]
        )
    representation: dict[str, Any] = {
        "reprtraceback": tb,
        "reprcrash": cr,
        "sections": error["captures"],
    }
    if error["text"]:
        representation["message"] = error["text"]
    if error["causes"]:
        representation["chain"] = [
            [
                traceback(c["frames"]),
                crash(c["exception"], c["frames"]),
                "The above exception was the direct cause of the following exception:"
                if c["relationship"] == "direct_cause"
                else "During handling of the above exception, another exception occurred:",
            ]
            for c in error["causes"]
        ] + [[tb, cr, None]]
    return {
        "exception": error["exception"],
        "repr_info": representation,
        "traceback": [
            {
                "filename": f["path"],
                "lineno": f["line"],
                "name": f["function"],
                "line": "\n".join(f["source"]),
            }
            for f in error["frames"]
        ],
        "reduction": error["reduction"],
    }
