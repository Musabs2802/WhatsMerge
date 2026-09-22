"""Core data structures shared across the WhatsMerge pipeline."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime

# Message "kinds" drive both bubble styling and statistics.
KIND_TEXT = "text"
KIND_SYSTEM = "system"
KIND_MEDIA = "media"
KIND_DELETED = "deleted"
KIND_CALL = "call"
KIND_LOCATION = "location"
KIND_POLL = "poll"
KIND_CONTACT = "contact"

# Attachment kinds map 1:1 onto renderers in app.js.
ATT_IMAGE = "image"
ATT_VIDEO = "video"
ATT_AUDIO = "audio"
ATT_VOICE = "voice"
ATT_STICKER = "sticker"
ATT_GIF = "gif"
ATT_DOCUMENT = "document"
ATT_CONTACT = "contact"
ATT_MISSING = "missing"

_WS = re.compile(r"\s+")
_INVISIBLE = re.compile(r"[​-‏⁦-⁩﻿­]")
# iOS prefixes exported media with an export-local sequence number that is
# renumbered on every re-export, so it must not take part in identity.
_IOS_SEQ = re.compile(r"^\d{6,10}-")


def normalize_text(value: str) -> str:
    """Whitespace/invisible-insensitive form used for equality checks."""
    return _WS.sub(" ", _INVISIBLE.sub("", value)).strip().casefold()


def media_identity(filename: str) -> str:
    """Filename stripped of export-local noise, for cross-export matching."""
    return _IOS_SEQ.sub("", filename.rsplit("/", 1)[-1]).casefold()


@dataclass
class Attachment:
    """A media file referenced by a message, resolved or not."""

    filename: str
    kind: str = ATT_MISSING
    mime: str = "application/octet-stream"
    size: int = 0
    #: Absolute path on disk while the pipeline runs; cleared before render.
    path: str | None = None
    #: data: URI or relative href, filled in by the media stage.
    src: str = ""
    #: sha1 of the file contents, when the file was actually found.
    content_hash: str = ""
    #: Set when the export said "media omitted" rather than naming a file.
    omitted: bool = False

    @property
    def resolved(self) -> bool:
        return self.path is not None

    @property
    def identity(self) -> str:
        return self.content_hash or media_identity(self.filename)

    def to_json(self) -> dict:
        data: dict = {"n": self.filename, "k": self.kind, "m": self.mime}
        if self.src:
            data["s"] = self.src
        if self.size:
            data["z"] = self.size
        if self.omitted:
            data["o"] = 1
        return data


@dataclass
class Message:
    """One line (possibly multi-line) of a WhatsApp conversation."""

    ts: datetime
    sender: str | None  # None means a system / notification line
    text: str
    kind: str = KIND_TEXT
    attachment: Attachment | None = None
    edited: bool = False
    #: False for Android exports, which only record hours and minutes.
    has_seconds: bool = True
    #: Labels of the export files this message was seen in.
    sources: set[str] = field(default_factory=set)
    #: True once the owner of the archive has been identified.
    outgoing: bool = False

    @property
    def is_system(self) -> bool:
        return self.sender is None

    def dedup_key(self) -> tuple[str, str, str]:
        """Identity used to collapse the same message seen in two exports.

        Deliberately minute-precision: iOS exports carry seconds and Android
        ones do not, so a second-precision key would never match across the
        two platforms.
        """
        stamp = self.ts.strftime("%Y-%m-%dT%H:%M")
        who = (self.sender or "\x00system").casefold()
        if self.attachment and not self.attachment.omitted:
            body = "\x01media:" + self.attachment.identity
        else:
            body = normalize_text(self.text)
        return (stamp, who, hashlib.sha1(body.encode("utf-8")).hexdigest()[:16])

    def slot_key(self) -> tuple[str, str]:
        """Coarser key: same minute, same sender. Used to pair a resolved
        attachment against a `<Media omitted>` placeholder for it."""
        return (self.ts.strftime("%Y-%m-%dT%H:%M"), (self.sender or "\x00system").casefold())

    def richness(self) -> tuple[int, int, int]:
        """Higher is better; decides which copy of a duplicate survives."""
        return (
            1 if (self.attachment and self.attachment.resolved) else 0,
            1 if self.has_seconds else 0,
            len(self.text),
        )


@dataclass
class ParsedChat:
    """The result of parsing a single export .txt file."""

    title: str
    source: str
    messages: list[Message] = field(default_factory=list)
    #: Non-fatal problems worth reporting to the user.
    warnings: list[str] = field(default_factory=list)

    @property
    def senders(self) -> set[str]:
        return {m.sender for m in self.messages if m.sender}


@dataclass
class Chat:
    """One conversation, stitched together from one or more exports."""

    title: str
    key: str
    messages: list[Message] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    is_group: bool = False
    owner: str | None = None

    @property
    def participants(self) -> list[str]:
        seen: dict[str, int] = {}
        for m in self.messages:
            if m.sender:
                seen[m.sender] = seen.get(m.sender, 0) + 1
        return sorted(seen, key=lambda s: (-seen[s], s))

    @property
    def start(self) -> datetime | None:
        return self.messages[0].ts if self.messages else None

    @property
    def end(self) -> datetime | None:
        return self.messages[-1].ts if self.messages else None
