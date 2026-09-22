"""Attach media to the output, either inlined or in a sibling folder."""

from __future__ import annotations

import base64
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .models import ATT_MISSING, Chat

#: Files larger than this are never inlined — a single 200 MB video would
#: make the HTML unopenable. They become download chips instead.
DEFAULT_MAX_INLINE_MB = 16.0

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class MediaReport:
    inlined: int = 0
    copied: int = 0
    skipped_large: int = 0
    missing: int = 0
    bytes_embedded: int = 0
    warnings: list[str] = field(default_factory=list)


def _safe_name(name: str, taken: set[str]) -> str:
    base = _SAFE_NAME.sub("_", Path(name).name) or "file"
    candidate, n = base, 2
    while candidate.casefold() in taken:
        stem, dot, ext = base.rpartition(".")
        candidate = f"{stem}_{n}{dot}{ext}" if dot else f"{base}_{n}"
        n += 1
    taken.add(candidate.casefold())
    return candidate


def attach_media(
    chats: list[Chat],
    mode: str = "inline",
    out_dir: Path | None = None,
    max_inline_mb: float = DEFAULT_MAX_INLINE_MB,
    media_dirname: str = "media",
) -> MediaReport:
    """Populate every attachment's `src` so the HTML can display it.

    `mode` is one of:
      inline   — base64 data URIs, producing one self-contained file
      external — copy files into `<out>/media/` and link relatively
      none     — no media in the output, only labelled placeholders
    """
    report = MediaReport()
    limit = int(max_inline_mb * 1024 * 1024)
    taken: set[str] = set()
    target_dir: Path | None = None
    # One copy per unique file, however many messages point at it.
    emitted: dict[str, str] = {}

    if mode == "external":
        if out_dir is None:
            raise ValueError("external media mode needs an output directory")
        target_dir = out_dir / media_dirname
        target_dir.mkdir(parents=True, exist_ok=True)

    for chat in chats:
        for msg in chat.messages:
            att = msg.attachment
            if att is None:
                continue
            if att.omitted or not att.filename:
                att.kind = ATT_MISSING
                report.missing += 1
                continue
            if not att.resolved:
                report.missing += 1
                att.src = ""
                continue

            source = Path(att.path)
            cache_key = att.content_hash or str(source)
            if cache_key in emitted:
                att.src = emitted[cache_key]
                continue

            if mode == "none":
                att.src = ""
                continue

            if mode == "external":
                name = _safe_name(att.filename, taken)
                try:
                    shutil.copy2(source, target_dir / name)
                except OSError as exc:
                    report.warnings.append(f"Could not copy {att.filename}: {exc}")
                    att.src = ""
                    continue
                att.src = f"{media_dirname}/{name}"
                report.copied += 1
            else:
                if att.size > limit:
                    report.skipped_large += 1
                    att.src = ""
                    continue
                try:
                    payload = source.read_bytes()
                except OSError as exc:
                    report.warnings.append(f"Could not read {att.filename}: {exc}")
                    att.src = ""
                    continue
                att.src = f"data:{att.mime};base64,{base64.b64encode(payload).decode('ascii')}"
                report.inlined += 1
                # base64 costs 4 bytes per 3 bytes of input.
                report.bytes_embedded += len(payload) * 4 // 3

            emitted[cache_key] = att.src

    if report.skipped_large:
        report.warnings.append(
            f"{report.skipped_large} file(s) exceeded --max-inline-mb and were left out; "
            f"use --media external to keep them"
        )
    return report


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} GB"
