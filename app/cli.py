"""Command line entry point for WhatsMerge."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from . import __version__
from .ingest import ingest
from .media import DEFAULT_MAX_INLINE_MB, attach_media, human_size
from .merge import build_chats, merge_stats
from .parser import parse_all
from .render import build_payload, write_html

DESCRIPTION = """\
Merge WhatsApp chat exports into one browsable, WhatsApp-styled HTML file.

Accepts the .zip archives WhatsApp emails you, the .txt transcripts inside
them, or folders containing either. Several exports of the same conversation
are stitched into a single timeline with the overlap removed.

Run with no arguments to read every export in ./exports and write
./output/archive.html.
"""

EPILOG = """\
examples:
  whatsmerge                              exports/ -> output/archive.html
  whatsmerge somewhere/else/              merge everything in another folder
  whatsmerge a.zip b.zip -o archive.html  merge two exports
  whatsmerge --media external             keep media beside the HTML
  whatsmerge --me "Musab Shaikh"          say who owns the archive
"""

#: Convention-over-configuration: drop exports in one folder, get one archive.
DEFAULT_INPUT_DIR = "exports"
DEFAULT_OUTPUT = "output/archive.html"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="whatsmerge",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("inputs", nargs="*", metavar="PATH",
                   help=".zip exports, .txt transcripts, or folders of them "
                        f"(default: ./{DEFAULT_INPUT_DIR})")
    p.add_argument("-o", "--output", default=DEFAULT_OUTPUT, metavar="FILE",
                   help="output HTML file (default: %(default)s)")
    p.add_argument("-t", "--title", default="", metavar="TEXT",
                   help="archive title shown in the sidebar")
    p.add_argument("--me", default=None, metavar="NAME",
                   help="your name as it appears in the exports; sets which "
                        "messages sit on the right. Auto-detected if omitted")
    p.add_argument("--media", choices=("inline", "external", "none"), default="inline",
                   help="inline: one self-contained file (default). "
                        "external: copy into <output>_files/media. none: omit media")
    p.add_argument("--max-inline-mb", type=float, default=DEFAULT_MAX_INLINE_MB,
                   metavar="N", help="skip inlining files larger than this "
                                     "(default: %(default)s MB)")
    p.add_argument("--date-order", choices=("auto", "dmy", "mdy", "ymd"), default="auto",
                   help="how to read ambiguous dates like 03/04/2026 (default: auto)")
    p.add_argument("--no-stitch", action="store_true",
                   help="only join exports whose conversation name matches "
                        "exactly; skip the content-based stitching that would "
                        "also join renamed or re-exported copies")
    p.add_argument("--ticks", choices=("delivered", "read", "sent", "none"), default="delivered",
                   help="delivery ticks to draw on your own messages. Exports "
                        "carry no read receipts, so this is presentation only "
                        "(default: %(default)s)")
    p.add_argument("--theme", choices=("auto", "light", "dark"), default="auto",
                   help="initial colour theme (default: %(default)s)")
    p.add_argument("--open", action="store_true", dest="open_after",
                   help="open the result in your browser when done")
    p.add_argument("-q", "--quiet", action="store_true", help="only report errors")
    p.add_argument("--version", action="version", version=f"whatsmerge {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    def say(*parts):
        if not args.quiet:
            print(*parts, flush=True)

    def warn(message: str) -> None:
        sys.stdout.flush()
        print(f"  ! {message}", file=sys.stderr, flush=True)

    out_path = Path(args.output).expanduser().resolve()
    if out_path.suffix.lower() not in (".html", ".htm"):
        out_path = out_path.with_suffix(".html")

    inputs = args.inputs
    if not inputs:
        default_dir = Path(DEFAULT_INPUT_DIR)
        if not default_dir.is_dir():
            print(f"No inputs given and ./{DEFAULT_INPUT_DIR} does not exist.\n"
                  f"Create it and drop your WhatsApp .zip exports in, or pass "
                  f"paths explicitly. See --help.", file=sys.stderr)
            return 2
        if not any(
            p.suffix.lower() in (".zip", ".txt")
            for p in default_dir.rglob("*") if p.is_file()
        ):
            print(f"./{DEFAULT_INPUT_DIR} has no .zip or .txt exports in it yet.\n"
                  f"Export a chat from WhatsApp on your phone and drop the .zip "
                  f"there — see {DEFAULT_INPUT_DIR}/README.md.", file=sys.stderr)
            return 2
        inputs = [str(default_dir)]
        say(f"Reading ./{DEFAULT_INPUT_DIR}…")
    else:
        say(f"Reading {len(inputs)} input path(s)…")

    loaded = ingest(inputs)
    try:
        for message in loaded.warnings:
            warn(message)
        if not loaded.units:
            print("No chat exports found. Point me at .zip files, .txt "
                  "transcripts, or a folder containing them.", file=sys.stderr)
            return 2

        say(f"  found {len(loaded.units)} export file(s)")

        parsed = parse_all(loaded.units, date_order=args.date_order)
        notes: list[str] = []
        for chat in parsed:
            for message in chat.warnings:
                warn(message)
                notes.append(message)

        chats, owner = build_chats(parsed, stitch=not args.no_stitch, owner_override=args.me)
        chats = [c for c in chats if c.messages]
        if not chats:
            print("Exports were read but contained no recognisable messages.", file=sys.stderr)
            return 2

        stats = merge_stats(chats)
        say(f"  {stats['messages']:,} messages across {stats['chats']} conversation(s)")
        if len(loaded.units) > len(chats):
            say(f"  stitched {len(loaded.units)} export(s) into {len(chats)} conversation(s)")
        if stats["overlapping"]:
            say(f"  {stats['overlapping']:,} message(s) appeared in more than one export")
        if owner:
            source = "given" if args.me else "detected"
            say(f"  archive owner ({source}): {owner}")
        else:
            warn("could not work out whose archive this is; use --me NAME")

        media_dir = out_path.parent / f"{out_path.stem}_files" if args.media == "external" else None
        report = attach_media(
            chats,
            mode=args.media,
            out_dir=media_dir,
            max_inline_mb=args.max_inline_mb,
        )
        for message in report.warnings:
            warn(message)
            notes.append(message)
        if args.media == "inline" and report.inlined:
            say(f"  embedded {report.inlined} media file(s), ~{human_size(report.bytes_embedded)}")
        elif args.media == "external" and report.copied:
            say(f"  copied {report.copied} media file(s) to {media_dir.name}/media")
        if report.missing:
            say(f"  {report.missing} attachment(s) had no file in the export")

        payload = build_payload(
            chats,
            owner=owner,
            title=args.title or _default_title(chats),
            ticks=args.ticks,
            theme=args.theme,
            notes=notes,
        )
        size = write_html(payload, out_path)
    finally:
        loaded.cleanup()

    say(f"\nWrote {out_path}  ({human_size(size)})")
    if args.media == "inline":
        say("Self-contained — the single file is all you need.")
    elif args.media == "external":
        say(f"Keep {out_path.name} next to {out_path.stem}_files/ or the media will not load.")

    if args.open_after:
        webbrowser.open(out_path.as_uri())
    return 0


def _default_title(chats) -> str:
    if len(chats) == 1:
        return chats[0].title
    return f"WhatsApp archive · {len(chats)} conversations"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
