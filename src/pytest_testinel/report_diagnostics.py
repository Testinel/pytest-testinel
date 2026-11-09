"""Read known pytest 7–9 reporting fields without asdict or re-rendering locals."""

from __future__ import annotations

from typing import Any

from .diagnostics import (
    MAX_DETAILS_BYTES,
    MAX_FRAME_LOCALS_BYTES,
    MAX_MESSAGE_BYTES,
    MAX_SOURCE_BYTES,
    MAX_VALUE_BYTES,
    normalize,
    reduction,
    truncate,
)


def report_representation(
    report: Any, *, value_limit: int = MAX_VALUE_BYTES
) -> dict[str, Any]:
    if isinstance(report, str):
        return {"message": truncate(report, MAX_DETAILS_BYTES)}

    meta = reduction(None)
    remaining_frames = 128

    def bounded(text: str, limit: int) -> str:
        result = truncate(text, limit)
        if result != text:
            meta["truncated_values"] += 1
        return result

    def lines(values: list[str], budget: int, per_value: int) -> list[str]:
        result = []
        remaining = budget
        for text in values:
            if remaining <= 0:
                meta["truncated_values"] += 1
                break
            part = bounded(text, min(per_value, remaining))
            result.append(part)
            remaining -= len(part.encode("utf-8", "replace"))
        return result

    def traceback(value: Any) -> dict[str, Any]:
        nonlocal remaining_frames
        entries = []
        raw = getattr(value, "reprentries", []) or []
        last_user = next(
            (
                i
                for i in range(len(raw) - 1, -1, -1)
                if "site-package"
                not in (getattr(getattr(raw[i], "reprfileloc", None), "path", "") or "")
            ),
            -1,
        )
        indices = sorted(
            set(range(min(len(raw), 512)))
            | {i for i in (len(raw) - 1, last_user) if i >= 0}
        )
        meta["omitted_frames"] += len(raw) - len(indices)
        for i in indices:
            entry = raw[i]
            detailed = remaining_frames > 0
            if detailed:
                remaining_frames -= 1
            if not detailed:
                meta["omitted_frames"] += 1
            loc = getattr(entry, "reprfileloc", None)
            local = getattr(entry, "reprlocals", None) if detailed else None
            args = getattr(entry, "reprfuncargs", None) if detailed else None
            meta["omitted_entries"] += max(
                0, len(getattr(local, "lines", []) or []) - 50
            )
            meta["omitted_entries"] += max(0, len(getattr(args, "args", []) or []) - 50)
            entries.append(
                {
                    "reprfileloc": {
                        "path": getattr(loc, "path", None),
                        "lineno": getattr(loc, "lineno", None),
                        "message": "",
                    },
                    "lines": lines(
                        (getattr(entry, "lines", []) or []) if detailed else [],
                        MAX_SOURCE_BYTES,
                        MAX_SOURCE_BYTES,
                    ),
                    "reprlocals": {
                        "lines": lines(
                            (getattr(local, "lines", []) or [])[:50],
                            MAX_FRAME_LOCALS_BYTES,
                            value_limit,
                        )
                    },
                    "reprfuncargs": {
                        "args": [
                            (n, bounded(v, value_limit))
                            for n, v in (getattr(args, "args", []) or [])[:50]
                        ]
                    },
                }
            )
        return {"reprentries": entries}

    def crash(value: Any) -> dict[str, Any]:
        return {
            "path": getattr(value, "path", None),
            "lineno": getattr(value, "lineno", None),
            "message": bounded(getattr(value, "message", "") or "", MAX_MESSAGE_BYTES),
        }

    main_tb = getattr(report, "reprtraceback", None)
    main_crash = getattr(report, "reprcrash", None)
    result: dict[str, Any] = {
        "reprtraceback": traceback(main_tb),
        "reprcrash": crash(main_crash),
        "sections": [
            [bounded(s, MAX_DETAILS_BYTES) for s in section[:2]]
            for section in (getattr(report, "sections", []) or [])[:50]
        ],
    }
    meta["omitted_entries"] += max(0, len(getattr(report, "sections", []) or []) - 50)
    chain = getattr(report, "chain", []) or []
    if len(chain) > 1:
        # Pytest 7–9 repeat the newest traceback in chain; read it only once.
        result["chain"] = [
            [traceback(tb), crash(cr), text] for tb, cr, text in chain[-17:-1]
        ] + [[result["reprtraceback"], result["reprcrash"], None]]
    meta["omitted_causes"] += max(0, len(chain) - 17)
    result["_reduction"] = meta
    return result


def from_report(
    report: Any,
    exception: BaseException | None,
    light_frames: list[dict[str, Any]],
    *,
    value_limit: int = MAX_VALUE_BYTES,
    frame_limit: int = MAX_FRAME_LOCALS_BYTES,
) -> dict[str, Any]:
    representation = report_representation(report, value_limit=value_limit)
    error = {
        "repr_info": representation,
        "reduction": representation.pop("_reduction", {}),
        "traceback": light_frames,
        "exception": {
            "type": f"{type(exception).__module__}.{type(exception).__name__}",
            "message": truncate(str(exception), MAX_MESSAGE_BYTES),
            "notes": [
                truncate(n, MAX_MESSAGE_BYTES)
                for n in (getattr(exception, "__notes__", []) or [])[:50]
            ],
        }
        if exception is not None
        else None,
    }
    canonical = normalize(error, value_limit=value_limit, frame_limit=frame_limit)
    canonical["reduction"]["omitted_entries"] += max(
        0, len(getattr(exception, "__notes__", []) or []) - 50
    )
    # Real exception identities and notes are more reliable than parsing reprcrash.
    previous: list[tuple[BaseException, str]] = []
    current = exception
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(previous) < 17:
        seen.add(id(current))
        if current.__cause__ is not None:
            current, relationship = current.__cause__, "direct_cause"
        elif not current.__suppress_context__ and current.__context__ is not None:
            current, relationship = current.__context__, "context"
        else:
            break
        previous.append((current, relationship))
    for cause, (exc, relationship) in zip(reversed(canonical["causes"]), previous):
        cause["exception"] = {
            "type": f"{type(exc).__module__}.{type(exc).__name__}",
            "message": truncate(str(exc), MAX_MESSAGE_BYTES),
            "notes": [
                truncate(n, MAX_MESSAGE_BYTES)
                for n in (getattr(exc, "__notes__", []) or [])[:50]
            ],
        }
        cause["relationship"] = relationship
    return dict(canonical)
