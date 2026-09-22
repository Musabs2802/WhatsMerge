"""Assemble the merged archive into one HTML document."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    KIND_CALL,
    KIND_CONTACT,
    KIND_DELETED,
    KIND_LOCATION,
    KIND_MEDIA,
    KIND_POLL,
    KIND_SYSTEM,
    KIND_TEXT,
    Chat,
)

ASSETS = Path(__file__).parent / "assets"

# Single-letter codes: on a 200k-message archive the long names would add
# megabytes to the payload for no benefit.
_KIND_CODE = {
    KIND_TEXT: "t",
    KIND_SYSTEM: "y",
    KIND_MEDIA: "m",
    KIND_DELETED: "d",
    KIND_CALL: "c",
    KIND_LOCATION: "l",
    KIND_POLL: "p",
    KIND_CONTACT: "v",
}


def _epoch(dt: datetime) -> int:
    """Seconds since the epoch, treating the naive export time as UTC.

    Exports carry wall-clock time with no zone. Pinning it to UTC and
    formatting with UTC getters in the browser means the rendered clock
    matches the transcript no matter where the file is opened.
    """
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def build_payload(
    chats: list[Chat],
    owner: str | None,
    title: str,
    ticks: str = "delivered",
    theme: str = "auto",
    notes: list[str] | None = None,
) -> dict:
    source_labels: list[str] = []
    source_index: dict[str, int] = {}

    def source_id(label: str) -> int:
        if label not in source_index:
            source_index[label] = len(source_labels)
            source_labels.append(label)
        return source_index[label]

    payload_chats = []
    for n, chat in enumerate(chats):
        people = chat.participants
        people_index = {name: i for i, name in enumerate(people)}
        msgs = []
        for msg in chat.messages:
            item: dict = {"t": _epoch(msg.ts)}
            if msg.sender is not None:
                item["w"] = people_index[msg.sender]
            code = _KIND_CODE.get(msg.kind, "t")
            if code != "t":
                item["k"] = code
            if msg.text:
                item["b"] = msg.text
            if msg.attachment is not None:
                item["a"] = msg.attachment.to_json()
            if msg.outgoing:
                item["o"] = 1
            if msg.edited:
                item["e"] = 1
            refs = sorted(source_id(s) for s in msg.sources)
            if refs:
                item["r"] = refs
            msgs.append(item)

        payload_chats.append(
            {
                "id": chat.key or f"chat{n}",
                "title": chat.title,
                "group": bool(chat.is_group),
                "people": people,
                "sources": sorted({source_id(s) for s in chat.sources}),
                "msgs": msgs,
            }
        )

    return {
        "title": title,
        "generated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "owner": owner,
        "ticks": ticks,
        "theme": theme,
        "sources": [{"label": label} for label in source_labels],
        "notes": notes or [],
        "chats": payload_chats,
    }


def _embed_json(payload: dict) -> str:
    """Serialise for a `<script type="application/json">` block.

    `</script>` anywhere in a message body would otherwise close the block
    early, and `<!--` would start a comment.
    """
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return text.replace("</", "<\\/").replace("<!--", "<\\!--")


def render_html(payload: dict) -> str:
    shell = (ASSETS / "shell.html").read_text(encoding="utf-8")
    # Tokens first: the component rules below them consume the variables.
    css = "\n".join(
        (ASSETS / name).read_text(encoding="utf-8") for name in ("tokens.css", "app.css")
    )
    js = (ASSETS / "app.js").read_text(encoding="utf-8")

    title = payload.get("title") or "WhatsApp archive"
    safe_title = (
        title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )

    # Ordered so that a token appearing inside injected content is never
    # itself substituted: title first, then CSS/JS, data last.
    html = shell.replace("__TITLE__", safe_title)
    html = html.replace("__CSS__", css)
    html = html.replace("__JS__", js)
    html = html.replace("__DATA__", _embed_json(payload))
    return html


def write_html(payload: dict, out_path: Path) -> int:
    html = render_html(payload)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return len(html.encode("utf-8"))
