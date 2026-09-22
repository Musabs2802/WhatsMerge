"""Stitch overlapping exports into one conversation per chat.

Two exports of the same chat taken on different days overlap heavily, arrive
with different media filenames, and — if they came from different phones —
different timestamp precision. This module decides which exports belong to
the same conversation, collapses the duplicates, and works out which
participant is the archive's owner so their messages can sit on the right.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter, defaultdict
from datetime import datetime

from .models import (
    KIND_MEDIA,
    KIND_SYSTEM,
    Chat,
    Message,
    ParsedChat,
    normalize_text,
)

_GROUP_HINTS = re.compile(
    r"(?:created (?:this )?group|added\b|left\b|removed\b|became an admin|"
    r"changed the subject|group description|criou o grupo|adicionou|saiu do grupo|"
    r"removeu|cre[óo] el grupo|a[ñn]adi[óo])",
    re.IGNORECASE,
)

# Titles the parser invents when the export carried no usable name.
_PLACEHOLDER_TITLE = re.compile(r"^(?:_chat|unknown chat|group \(\d+ participants\)|.+ / .+)$", re.I)

#: Fraction of the smaller export's messages that must also appear in the
#: larger one before the two are treated as the same conversation.
OVERLAP_THRESHOLD = 0.15


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().casefold()


class _Union:
    """Minimal union-find over parsed-chat indices."""

    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _looks_like_group(parsed: ParsedChat) -> bool:
    if len(parsed.senders) > 2:
        return True
    return any(
        m.kind == KIND_SYSTEM and _GROUP_HINTS.search(m.text) for m in parsed.messages
    )


def _overlap(a: ParsedChat, b: ParsedChat) -> float:
    keys_a = {m.dedup_key() for m in a.messages}
    keys_b = {m.dedup_key() for m in b.messages}
    smaller = min(len(keys_a), len(keys_b))
    if smaller == 0:
        return 0.0
    return len(keys_a & keys_b) / smaller


def group_exports(parsed: list[ParsedChat], stitch: bool = True) -> list[list[ParsedChat]]:
    """Partition parsed exports into groups that describe the same chat."""
    if not parsed:
        return []

    uf = _Union(len(parsed))
    if not stitch:
        # Still fold together exports whose titles match exactly; anything
        # less certain stays separate.
        by_title: dict[str, int] = {}
        for i, p in enumerate(parsed):
            key = _norm_title(p.title)
            if _PLACEHOLDER_TITLE.match(p.title):
                continue
            if key in by_title:
                uf.union(by_title[key], i)
            else:
                by_title[key] = i
        return _collect(parsed, uf)

    titles = [_norm_title(p.title) for p in parsed]
    groupish = [_looks_like_group(p) for p in parsed]
    senders = [frozenset(s.casefold() for s in p.senders) for p in parsed]

    for i in range(len(parsed)):
        for j in range(i + 1, len(parsed)):
            if uf.find(i) == uf.find(j):
                continue
            same_title = (
                titles[i] == titles[j]
                and titles[i]
                and not _PLACEHOLDER_TITLE.match(parsed[i].title)
            )
            # Near-identical titles ("Ahmed Al-Rashid" vs "Ahmed Al Rashid").
            close_title = (
                not same_title
                and titles[i]
                and titles[j]
                and difflib.SequenceMatcher(None, titles[i], titles[j]).ratio() > 0.92
            )
            same_people = bool(senders[i]) and senders[i] == senders[j]
            one_to_one = same_people and not groupish[i] and not groupish[j]

            if same_title or one_to_one:
                uf.union(i, j)
                continue
            # Groups can share a roster without being the same group, and a
            # fuzzy title match can be a coincidence, so both need evidence
            # from the transcript itself.
            if (close_title or same_people) and _overlap(parsed[i], parsed[j]) >= OVERLAP_THRESHOLD:
                uf.union(i, j)

    return _collect(parsed, uf)


def _collect(parsed: list[ParsedChat], uf: _Union) -> list[list[ParsedChat]]:
    buckets: dict[int, list[ParsedChat]] = defaultdict(list)
    for i, p in enumerate(parsed):
        buckets[uf.find(i)].append(p)
    return [buckets[k] for k in sorted(buckets)]


def dedup(messages: list[Message]) -> list[Message]:
    """Collapse messages seen in more than one export.

    Runs in two passes. The first matches on timestamp, sender and content.
    The second pairs a `<Media omitted>` placeholder from one export against
    the same message with its file attached in another, which the first pass
    cannot see because their bodies differ.
    """
    best: dict[tuple[str, str, str], Message] = {}
    order: dict[tuple[str, str, str], int] = {}
    for idx, msg in enumerate(messages):
        key = msg.dedup_key()
        existing = best.get(key)
        if existing is None:
            best[key] = msg
            order[key] = idx
        else:
            keeper, loser = (
                (msg, existing) if msg.richness() > existing.richness() else (existing, msg)
            )
            keeper.sources |= loser.sources
            best[key] = keeper

    kept = sorted(best.values(), key=lambda m: order[m.dedup_key()])

    # Second pass: placeholder vs. real attachment in the same minute.
    slots: dict[tuple[str, str], list[Message]] = defaultdict(list)
    for msg in kept:
        if msg.kind == KIND_MEDIA and msg.attachment is not None:
            slots[msg.slot_key()].append(msg)

    drop: set[int] = set()
    for bucket in slots.values():
        placeholders = [m for m in bucket if m.attachment.omitted]
        concrete = [m for m in bucket if not m.attachment.omitted]
        if not placeholders or not concrete:
            continue
        for placeholder, real in zip(placeholders, concrete):
            real.sources |= placeholder.sources
            drop.add(id(placeholder))

    return [m for m in kept if id(m) not in drop]


def detect_owner(chats: list[Chat], override: str | None = None) -> str | None:
    """Identify whose archive this is, so their messages align right.

    Exports do not mark the owner. Two signals do: the owner appears across
    more conversations than anyone else, and a 1:1 chat is named after the
    *other* party, never the owner.
    """
    everyone: Counter[str] = Counter()
    chat_count: Counter[str] = Counter()
    titled_as_other: Counter[str] = Counter()

    for chat in chats:
        seen = set()
        title = _norm_title(chat.title)
        for msg in chat.messages:
            if not msg.sender:
                continue
            everyone[msg.sender] += 1
            seen.add(msg.sender)
        for name in seen:
            chat_count[name] += 1
            if _norm_title(name) == title:
                titled_as_other[name] += 1

    if not everyone:
        return None

    if override:
        target = _norm_title(override)
        for name in everyone:
            if _norm_title(name) == target:
                return name
        close = difflib.get_close_matches(target, [_norm_title(n) for n in everyone], 1, 0.75)
        if close:
            for name in everyone:
                if _norm_title(name) == close[0]:
                    return name
        # Honour the override even if it never sent a message.
        return override

    def score(name: str) -> tuple[int, int]:
        return (chat_count[name] - titled_as_other[name], everyone[name])

    return max(everyone, key=score)


def _title_for(bucket: list[ParsedChat], chat: Chat, owner: str | None) -> str:
    """Pick the most informative title the exports offer."""
    real = [p.title for p in bucket if not _PLACEHOLDER_TITLE.match(p.title)]
    if real:
        return Counter(real).most_common(1)[0][0]
    others = [p for p in chat.participants if p != owner]
    if not chat.is_group and len(others) == 1:
        return others[0]
    if others:
        return ", ".join(others[:3]) + (f" +{len(others) - 3}" if len(others) > 3 else "")
    return bucket[0].title


def build_chats(
    parsed: list[ParsedChat], stitch: bool = True, owner_override: str | None = None
) -> tuple[list[Chat], str | None]:
    """Merge parsed exports into finished chats and resolve the owner."""
    chats: list[Chat] = []
    buckets = group_exports(parsed, stitch=stitch)

    for bucket in buckets:
        # Concatenating in source order keeps same-minute messages in the
        # order the transcript recorded them; the sort below is stable.
        combined: list[Message] = []
        for part in bucket:
            combined.extend(part.messages)
        combined = dedup(combined)
        combined.sort(key=lambda m: m.ts)

        chat = Chat(
            title=bucket[0].title,
            key="",
            messages=combined,
            sources=[p.source for p in bucket],
            is_group=any(_looks_like_group(p) for p in bucket),
        )
        if not chat.is_group and len(chat.participants) > 2:
            chat.is_group = True
        chats.append(chat)

    owner = detect_owner(chats, owner_override)

    for chat, bucket in zip(chats, buckets):
        chat.owner = owner
        chat.title = _title_for(bucket, chat, owner)
        chat.key = _norm_title(chat.title) or f"chat-{id(chat)}"
        for msg in chat.messages:
            msg.outgoing = msg.sender is not None and msg.sender == owner

    # Most recent conversation first, like WhatsApp's chat list.
    chats.sort(key=lambda c: c.end or datetime.min, reverse=True)
    _dedupe_keys(chats)
    return chats, owner


def _dedupe_keys(chats: list[Chat]) -> None:
    seen: Counter[str] = Counter()
    for chat in chats:
        seen[chat.key] += 1
        if seen[chat.key] > 1:
            chat.key = f"{chat.key}-{seen[chat.key]}"


def merge_stats(chats: list[Chat]) -> dict:
    """Headline numbers for the CLI summary."""
    total = sum(len(c.messages) for c in chats)
    duplicates = sum(
        1 for c in chats for m in c.messages if len(m.sources) > 1
    )
    media = sum(
        1 for c in chats for m in c.messages if m.attachment is not None
    )
    resolved = sum(
        1
        for c in chats
        for m in c.messages
        if m.attachment is not None and m.attachment.resolved
    )
    return {
        "chats": len(chats),
        "messages": total,
        "overlapping": duplicates,
        "media": media,
        "media_resolved": resolved,
    }


__all__ = [
    "build_chats",
    "dedup",
    "detect_owner",
    "group_exports",
    "merge_stats",
    "normalize_text",
]
