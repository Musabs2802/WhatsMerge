"""Discover export files and unpack them into a working directory."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

TEXT_SUFFIX = ".txt"
ZIP_SUFFIX = ".zip"

# "WhatsApp Chat with Ahmed Al-Rashid" / "Conversa do WhatsApp com Ahmed"
_TITLE_PREFIXES = re.compile(
    r"^(?:whatsapp\s+chat\s+(?:with|-)\s*"
    r"|conversa\s+do\s+whatsapp\s+com\s*"
    r"|chat\s+de\s+whatsapp\s+con\s*"
    r"|whatsapp[\s_-]*chat[\s_-]*"
    r")",
    re.IGNORECASE,
)


@dataclass
class ExportUnit:
    """A single chat export: one transcript plus the media beside it."""

    #: Human-readable provenance label shown in the UI.
    label: str
    #: Path to the transcript on disk.
    transcript: Path
    #: Directory to search for attachment filenames.
    media_root: Path
    #: Best guess at the conversation title, from the archive or file name.
    hint_title: str = ""


@dataclass
class Ingestion:
    units: list[ExportUnit] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    _workdir: Path | None = None

    def cleanup(self) -> None:
        if self._workdir and self._workdir.exists():
            shutil.rmtree(self._workdir, ignore_errors=True)


def clean_title(raw: str) -> str:
    """Turn a file or archive name into a conversation title."""
    name = Path(raw).stem
    name = _TITLE_PREFIXES.sub("", name).strip(" -_")
    # Trailing export counters: "Ahmed Al-Rashid (2)", "Ahmed_2026-03-12"
    name = re.sub(r"\s*\(\d+\)$", "", name)
    name = re.sub(r"[\s_-]+\d{4}-\d{2}-\d{2}$", "", name)
    name = name.replace("_", " ").strip()
    return name or "Unknown chat"


def _zip_member_name(info: zipfile.ZipInfo) -> str:
    """Recover a member name that Python decoded with the wrong codec.

    Zip entries without the UTF-8 flag are decoded as cp437, which mangles
    non-ASCII contact names. Round-tripping recovers the real bytes.
    """
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract every member under `dest`, defusing path traversal."""
    dest = dest.resolve()
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = _zip_member_name(info)
        # Flatten any directory structure onto a sanitised relative path.
        parts = [p for p in re.split(r"[\\/]+", name) if p not in ("", ".", "..")]
        if not parts:
            continue
        target = (dest / Path(*parts)).resolve()
        if not str(target).startswith(str(dest) + os.sep) and target != dest:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            shutil.copyfileobj(src, out)


def _units_from_dir(root: Path, label_base: str, hint: str) -> list[ExportUnit]:
    """Every transcript inside `root` becomes a unit rooted at its folder."""
    units: list[ExportUnit] = []
    transcripts = sorted(p for p in root.rglob("*") if p.suffix.lower() == TEXT_SUFFIX)
    for tx in transcripts:
        # `_chat.txt` carries no name; fall back to the archive's own name.
        stem = tx.stem
        title = hint if stem.startswith("_chat") else clean_title(stem)
        label = label_base if len(transcripts) == 1 else f"{label_base}/{tx.name}"
        units.append(
            ExportUnit(
                label=label,
                transcript=tx,
                media_root=tx.parent,
                hint_title=title or hint,
            )
        )
    return units


def ingest(paths: list[str]) -> Ingestion:
    """Resolve CLI inputs into transcript + media pairs.

    Accepts any mix of `.zip` archives, loose `.txt` transcripts, and
    directories containing either.
    """
    result = Ingestion()
    workdir = Path(tempfile.mkdtemp(prefix="whatsmerge-"))
    result._workdir = workdir

    queue: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if not p.exists():
            result.warnings.append(f"Input not found, skipped: {raw}")
            continue
        if p.is_dir():
            found = sorted(
                c
                for c in p.rglob("*")
                if c.is_file() and c.suffix.lower() in (ZIP_SUFFIX, TEXT_SUFFIX)
            )
            if not found:
                result.warnings.append(f"No .zip or .txt files under {p}")
            queue.extend(found)
        else:
            queue.append(p)

    used_labels: set[str] = set()

    def unique(label: str) -> str:
        candidate, n = label, 2
        while candidate in used_labels:
            candidate = f"{label} ({n})"
            n += 1
        used_labels.add(candidate)
        return candidate

    for path in queue:
        suffix = path.suffix.lower()
        if suffix == ZIP_SUFFIX:
            hint = clean_title(path.name)
            dest = workdir / f"{len(used_labels):03d}-{re.sub(r'[^A-Za-z0-9]+', '_', path.stem)[:40]}"
            dest.mkdir(parents=True, exist_ok=True)
            try:
                with zipfile.ZipFile(path) as zf:
                    _safe_extract(zf, dest)
            except (zipfile.BadZipFile, OSError) as exc:
                result.warnings.append(f"Could not read archive {path.name}: {exc}")
                continue
            units = _units_from_dir(dest, unique(path.name), hint)
            if not units:
                result.warnings.append(f"No chat transcript (.txt) inside {path.name}")
            result.units.extend(units)
        elif suffix == TEXT_SUFFIX:
            hint = clean_title(path.name)
            result.units.append(
                ExportUnit(
                    label=unique(path.name),
                    transcript=path,
                    media_root=path.parent,
                    hint_title=hint,
                )
            )
        else:
            result.warnings.append(f"Unsupported input type, skipped: {path.name}")

    return result
