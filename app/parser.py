"""Turn a WhatsApp export transcript into structured messages.

WhatsApp has no stable export format. The shape varies by platform (iOS
brackets the timestamp, Android follows it with " - "), by locale (date
order, separator, AM/PM markers, translated system strings) and by script
(Arabic locales emit Arabic-Indic digits). Everything here exists to absorb
that variation without guessing at message content.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from .ingest import ExportUnit
from .models import (
    ATT_MISSING,
    KIND_CALL,
    KIND_CONTACT,
    KIND_DELETED,
    KIND_LOCATION,
    KIND_MEDIA,
    KIND_POLL,
    KIND_SYSTEM,
    KIND_TEXT,
    Attachment,
    Message,
    ParsedChat,
)

# --------------------------------------------------------------------------
# Unicode normalisation
# --------------------------------------------------------------------------

LRM = "‎"  # left-to-right mark; iOS prefixes system + media lines with it
RLM = "‏"

# Arabic-Indic and Extended Arabic-Indic digits -> ASCII, plus the narrow and
# non-breaking spaces iOS 17+ puts before AM/PM.
_TS_TRANSLATION = {ord(" "): " ", ord(" "): " "}
for _base in (0x0660, 0x06F0):
    for _i in range(10):
        _TS_TRANSLATION[_base + _i] = ord("0") + _i

# Arabic meridiem markers: ص (morning) / م (evening).
_MERIDIEM = {"ص": "AM", "م": "PM", "ص.": "AM", "م.": "PM"}

_STRIP_MARKS = re.compile(r"[​-‏⁦-⁩﻿]")


def _normalize_ts(raw: str) -> str:
    return raw.translate(_TS_TRANSLATION).strip()


# --------------------------------------------------------------------------
# Line shapes
# --------------------------------------------------------------------------

# iOS: "[12/03/2026, 14:23:45] Ahmed Al-Rashid: hello"
_BRACKET_RE = re.compile(r"^[‎‏]*\[(?P<ts>[^\]]{6,44})\][   ]*(?P<rest>.*)$", re.S)
# Android: "12/03/2026, 14:23 - Ahmed Al-Rashid: hello"
_DASH_RE = re.compile(r"^[‎‏]*(?P<ts>\d[^\n]{5,42}?) - (?P<rest>.*)$", re.S)

# Timestamp components, order-agnostic.
_TS_RE = re.compile(
    r"""^\s*
    (?P<d1>\d{1,4})[./\-](?P<d2>\d{1,2})[./\-](?P<d3>\d{2,4})
    [,\s]+
    (?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}))?
    (?:\s*(?P<ampm>[APap]\.?[Mm]\.?|ص\.?|م\.?))?
    \s*$""",
    re.X,
)

# --------------------------------------------------------------------------
# Body markers
# --------------------------------------------------------------------------

# iOS: "<attached: 00000042-PHOTO-2026-03-12-14-23-45.jpg>" and translations
# ("anexado", "adjunto", "allegato", "joint", "مرفق"...). The label is matched
# loosely because only the filename matters.
_ATTACHED_RE = re.compile(r"^[‎‏]*<[^<>:]{1,24}:\s*(?P<fn>[^<>]+?)\s*>[‎‏]*$")
# Android: "IMG-20260312-WA0001.jpg (file attached)". The parenthetical must
# read as an attachment note, or an ordinary sentence ending in "(see spec.pdf
# attached)" would be swallowed as a filename.
_FILE_ATTACHED_RE = re.compile(
    r"^[‎‏]*(?P<fn>[^\s<>][^\n]*?\.[A-Za-z0-9]{2,5})\s*"
    r"\([^()]{0,24}(?:attach|anexad|adjunt|allegat|angeh|bijgevoeg|bifoga|"
    r"liitte|załącz|прикреп|"
    r"مرفق|添付)[^()]{0,24}\)\s*$",
    re.IGNORECASE,
)

_OMITTED_RE = re.compile(
    r"^[‎‏]*(?:<\s*)?"
    r"(?:media|image|photo|video|audio|sticker|gif|document|contact card|imagem|"
    r"v[ií]deo|[áa]udio|figurinha|documento|imagen|foto|adjunto|multimedia)"
    r"[^\n]{0,24}?"
    r"(?:omitted|omitida|omitido|ausente|weggelassen|omis)"
    r"(?:\s*>)?[‎‏]*\s*$",
    re.IGNORECASE,
)

_DELETED_RE = re.compile(
    r"^[‎‏]*(?:this message was deleted|you deleted this message|"
    r"esta mensagem foi apagada|voc[eê] apagou esta mensagem|"
    r"se elimin[oó] este mensaje|eliminaste este mensaje)\.?\s*$",
    re.IGNORECASE,
)

_EDITED_RE = re.compile(
    r"[‎‏]*<\s*(?:this message was edited|esta mensagem foi editada|"
    r"se edit[oó] este mensaje)\s*>\s*$",
    re.IGNORECASE,
)

_CALL_RE = re.compile(
    r"^[‎‏]*(?:missed (?:voice|video|group) call|chamada de (?:voz|v[ií]deo) perdida|"
    r"llamada (?:de voz|de v[ií]deo) perdida|voice call|video call)\b",
    re.IGNORECASE,
)

_LOCATION_RE = re.compile(r"(?:maps\.google\.com/\?q=|google\.com/maps)", re.IGNORECASE)
_POLL_RE = re.compile(r"^[‎‏]*POLL:", re.IGNORECASE)

# Phrases that identify a notification even when a naive "Name: body" split
# would succeed. Only consulted for lines that are ambiguous.
_SYSTEM_HINTS = re.compile(
    r"(?:end-to-end encrypted|created (?:this )?group|added you|changed the subject|"
    r"changed this group's icon|changed their phone number|joined using|left\b|"
    r"removed\b|became an admin|security code changed|are end-to-end encrypted|"
    r"criptografad[ao]|criou o grupo|adicionou|saiu\b|removeu|mudou o assunto|"
    r"cifrad[ao]s de extremo a extremo|cre[óo] el grupo|a[ñn]adi[óo]|sali[óo])",
    re.IGNORECASE,
)

# A sender name longer than this is almost certainly a mis-split system line.
_MAX_SENDER_LEN = 72

_MEDIA_EXT = {
    "jpg": ("image", "image/jpeg"),
    "jpeg": ("image", "image/jpeg"),
    "png": ("image", "image/png"),
    "gif": ("gif", "image/gif"),
    "webp": ("sticker", "image/webp"),
    "heic": ("image", "image/heic"),
    "bmp": ("image", "image/bmp"),
    "mp4": ("video", "video/mp4"),
    "mov": ("video", "video/quicktime"),
    "3gp": ("video", "video/3gpp"),
    "webm": ("video", "video/webm"),
    "avi": ("video", "video/x-msvideo"),
    "opus": ("voice", "audio/ogg"),
    "ogg": ("voice", "audio/ogg"),
    "m4a": ("audio", "audio/mp4"),
    "aac": ("audio", "audio/aac"),
    "mp3": ("audio", "audio/mpeg"),
    "wav": ("audio", "audio/wav"),
    "amr": ("audio", "audio/amr"),
    "vcf": ("contact", "text/vcard"),
    "pdf": ("document", "application/pdf"),
}

# Android voice notes are named PTT-*, iOS ones AUDIO-*.
_VOICE_HINT = re.compile(r"(?:^|[-_])(?:PTT|AUDIO)[-_]", re.IGNORECASE)


def classify_attachment(filename: str) -> tuple[str, str]:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    kind, mime = _MEDIA_EXT.get(ext, ("document", "application/octet-stream"))
    if kind == "audio" and _VOICE_HINT.search(Path(filename).name):
        kind = "voice"
    return kind, mime


# --------------------------------------------------------------------------
# Timestamps
# --------------------------------------------------------------------------


class TimestampReader:
    """Parses timestamps after sniffing the file's day/month order.

    A single line is ambiguous ("03/04" could be either order), so the order
    is decided from the whole file: any component above 12 settles it.
    """

    def __init__(self, order: str = "auto") -> None:
        self.requested = order
        self.order = order if order != "auto" else "dmy"
        self._sniffed = order != "auto"

    def sniff(self, raw_stamps: list[str]) -> str:
        if self._sniffed:
            return self.order
        first_over_12 = second_over_12 = False
        for raw in raw_stamps:
            m = _TS_RE.match(_normalize_ts(raw))
            if not m:
                continue
            d1, d2 = int(m.group("d1")), int(m.group("d2"))
            if len(m.group("d1")) == 4:
                self.order, self._sniffed = "ymd", True
                return self.order
            if d1 > 12:
                first_over_12 = True
            if d2 > 12:
                second_over_12 = True
        if first_over_12 and not second_over_12:
            self.order = "dmy"
        elif second_over_12 and not first_over_12:
            self.order = "mdy"
        # Otherwise every date is ambiguous; dmy is WhatsApp's majority locale.
        self._sniffed = True
        return self.order

    def parse(self, raw: str) -> datetime | None:
        m = _TS_RE.match(_normalize_ts(raw))
        if not m:
            return None
        d1, d2, d3 = m.group("d1"), m.group("d2"), m.group("d3")
        if len(d1) == 4:
            year, month, day = int(d1), int(d2), int(d3)
        elif self.order == "mdy":
            month, day, year = int(d1), int(d2), int(d3)
        elif self.order == "ymd":
            year, month, day = int(d1), int(d2), int(d3)
        else:
            day, month, year = int(d1), int(d2), int(d3)
        if year < 100:
            year += 2000 if year < 70 else 1900

        hour, minute = int(m.group("h")), int(m.group("mi"))
        second = int(m.group("s") or 0)
        ampm = (m.group("ampm") or "").strip()
        if ampm:
            token = _MERIDIEM.get(ampm) or _MERIDIEM.get(ampm[0]) or ampm.replace(".", "").upper()
            if token.startswith("P") and hour < 12:
                hour += 12
            elif token.startswith("A") and hour == 12:
                hour = 0
        try:
            return datetime(year, month, day, hour, minute, second)
        except ValueError:
            return None

    @property
    def has_seconds_hint(self) -> bool:
        return True


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _split_header(line: str) -> tuple[str, str] | None:
    """Return (raw_timestamp, remainder) for a line that starts a message."""
    m = _BRACKET_RE.match(line)
    if m and _TS_RE.match(_normalize_ts(m.group("ts"))):
        return m.group("ts"), m.group("rest")
    m = _DASH_RE.match(line)
    if m and _TS_RE.match(_normalize_ts(m.group("ts"))):
        return m.group("ts"), m.group("rest")
    return None


def _split_sender(rest: str) -> tuple[str | None, str]:
    """Separate "Sender: body" from a bare notification line.

    iOS marks notifications with a leading LRM, which is authoritative.
    Elsewhere the split is guarded: a name cannot be arbitrarily long and a
    line reading like a group notification is treated as one.
    """
    if rest.startswith(LRM) or rest.startswith(RLM):
        stripped = rest.lstrip(LRM + RLM)
        # iOS also LRM-marks media lines, which *do* have a sender before the
        # colon — but there the LRM sits after the colon, not before it. A
        # leading mark therefore means a notification.
        return None, stripped

    head, sep, tail = rest.partition(": ")
    if not sep:
        return None, rest
    name = _STRIP_MARKS.sub("", head).strip()
    if not name or len(name) > _MAX_SENDER_LEN or "\n" in name:
        return None, rest
    if _SYSTEM_HINTS.search(head):
        return None, rest
    return name, tail


def _build_body(ts: datetime, sender: str | None, body: str, has_seconds: bool, source: str) -> Message:
    text = body
    edited = bool(_EDITED_RE.search(text))
    if edited:
        text = _EDITED_RE.sub("", text).rstrip()

    stripped = _STRIP_MARKS.sub("", text).strip()
    kind = KIND_SYSTEM if sender is None else KIND_TEXT
    attachment: Attachment | None = None

    if sender is not None:
        # A captioned photo puts the marker on the first line and the caption
        # on the ones after it, so only the first line is tested.
        first, _, remainder = text.partition("\n")
        first = first.strip()
        caption = remainder.strip()

        m = _ATTACHED_RE.match(first) or _FILE_ATTACHED_RE.match(first)
        if m:
            filename = _STRIP_MARKS.sub("", m.group("fn")).strip()
            akind, mime = classify_attachment(filename)
            attachment = Attachment(filename=filename, kind=akind, mime=mime)
            kind = KIND_CONTACT if akind == "contact" else KIND_MEDIA
            text = caption
        elif _OMITTED_RE.match(first):
            attachment = Attachment(
                filename="", kind=ATT_MISSING, mime="", omitted=True
            )
            kind = KIND_MEDIA
            text = caption
        elif _DELETED_RE.match(stripped):
            kind = KIND_DELETED
            text = stripped
        elif _POLL_RE.match(stripped):
            kind = KIND_POLL
        elif _CALL_RE.match(stripped):
            kind = KIND_CALL
        elif _LOCATION_RE.search(stripped):
            kind = KIND_LOCATION

    return Message(
        ts=ts,
        sender=sender,
        text=text,
        kind=kind,
        attachment=attachment,
        edited=edited,
        has_seconds=has_seconds,
        sources={source},
    )


def parse_transcript(unit: ExportUnit, date_order: str = "auto") -> ParsedChat:
    """Parse one export file into a `ParsedChat`."""
    content = _read_text(unit.transcript)
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    headers: list[tuple[str, str] | None] = [_split_header(line) for line in lines]
    reader = TimestampReader(date_order)
    reader.sniff([h[0] for h in headers if h])

    chat = ParsedChat(title=unit.hint_title or unit.transcript.stem, source=unit.label)

    pending: Message | None = None
    pending_parts: list[str] = []
    unparsed = 0

    def flush() -> None:
        nonlocal pending, pending_parts
        if pending is None:
            return
        if pending_parts:
            extra = "\n".join(pending_parts).rstrip()
            pending.text = (pending.text + "\n" + extra).strip() if pending.text else extra
        chat.messages.append(pending)
        pending, pending_parts = None, []

    for line, header in zip(lines, headers):
        if header is None:
            if pending is not None:
                pending_parts.append(line)
            elif line.strip():
                unparsed += 1
            continue
        raw_ts, rest = header
        ts = reader.parse(raw_ts)
        if ts is None:
            if pending is not None:
                pending_parts.append(line)
            else:
                unparsed += 1
            continue
        flush()
        sender, body = _split_sender(rest)
        has_seconds = ":" in raw_ts and _TS_RE.match(_normalize_ts(raw_ts)).group("s") is not None
        pending = _build_body(ts, sender, body, has_seconds, unit.label)

    flush()

    _reclassify_rare_senders(chat)
    _resolve_media(chat, unit)

    if unparsed:
        chat.warnings.append(
            f"{unit.label}: {unparsed} line(s) before the first timestamp were ignored"
        )
    if not chat.messages:
        chat.warnings.append(f"{unit.label}: no messages recognised — unsupported format?")
    if reader.requested == "auto" and reader.order == "dmy" and _dates_ambiguous(chat):
        chat.warnings.append(
            f"{unit.label}: every date is ambiguous; assumed day/month order "
            f"— pass --date-order mdy if that is wrong"
        )

    chat.title = _refine_title(chat, unit)
    return chat


def _dates_ambiguous(chat: ParsedChat) -> bool:
    return not any(m.ts.day > 12 for m in chat.messages)


def _reclassify_rare_senders(chat: ParsedChat) -> None:
    """Demote one-off "senders" that look like mis-split notifications.

    A real participant sends more than one message; a line like
    `Ahmed changed the subject to "Q3: Halal certs"` yields a bogus sender
    seen exactly once.
    """
    counts = Counter(m.sender for m in chat.messages if m.sender)
    if not counts:
        return
    regulars = {name for name, n in counts.items() if n > 1}
    for msg in chat.messages:
        if msg.sender and msg.sender not in regulars and _SYSTEM_HINTS.search(msg.sender):
            msg.text = f"{msg.sender}: {msg.text}".strip(": ").strip()
            msg.sender = None
            msg.kind = KIND_SYSTEM


def _resolve_media(chat: ParsedChat, unit: ExportUnit) -> None:
    """Point each attachment at a real file beside the transcript."""
    index: dict[str, Path] = {}
    try:
        for path in unit.media_root.rglob("*"):
            if path.is_file() and path.suffix.lower() != ".txt":
                index.setdefault(path.name.casefold(), path)
    except OSError:
        return
    for msg in chat.messages:
        att = msg.attachment
        if att is None or att.omitted or not att.filename:
            continue
        found = index.get(Path(att.filename).name.casefold())
        if found is not None:
            att.path = str(found)
            try:
                att.size = found.stat().st_size
            except OSError:
                att.size = 0
            # Hashing here rather than in the media stage: cross-export
            # de-duplication needs it, and that runs first.
            att.content_hash = _hash_file(found)


def _hash_file(path: Path, limit: int = 1 << 20) -> str:
    """sha1 of the file's leading bytes — enough to identify the same photo
    re-exported under a different sequence number."""
    digest = hashlib.sha1()
    try:
        with open(path, "rb") as fh:
            digest.update(fh.read(limit))
    except OSError:
        return ""
    return digest.hexdigest()[:20]


def _refine_title(chat: ParsedChat, unit: ExportUnit) -> str:
    """Prefer a real contact name over a placeholder like `_chat`."""
    title = (unit.hint_title or "").strip()
    if title and not title.lower().startswith("_chat"):
        return title
    senders = Counter(m.sender for m in chat.messages if m.sender)
    if len(senders) == 2:
        # In a 1:1 export the chat is named after the other party; without an
        # owner yet, fall back to both names and let the merge stage rename.
        return " / ".join(sorted(senders))
    if senders:
        return f"Group ({len(senders)} participants)"
    return unit.transcript.stem


def parse_all(units: list[ExportUnit], date_order: str = "auto") -> list[ParsedChat]:
    return [parse_transcript(u, date_order) for u in units]
