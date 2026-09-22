# WhatsMerge

Merge WhatsApp chat exports — several conversations, or several partial exports
of the same conversation — into one self-contained HTML file that reads like
WhatsApp Web.

No dependencies. No network calls. Python 3.10+ and nothing else.

## Quick start

1. Drop your WhatsApp `.zip` exports into [`exports/`](exports/).
2. Double-click **`merge.bat`** (Windows) or run `python -m app`.
3. Read `output/archive.html`.

That's it. No arguments needed — `exports/` in, `output/archive.html` out.

```
WhatsMerge/
├─ exports/          ← put your .zip exports here   (never committed)
├─ output/           ← the archive lands here       (never committed)
├─ merge.bat         ← double-click to build
├─ merge.ps1         ← same, with options
├─ app/              ← the program
├─ tests/
└─ tools/
```

[`exports/README.md`](exports/README.md) explains how to get an export off your
phone.

## What it does

WhatsApp exports one conversation at a time, and a re-export of the same chat
overlaps everything you already had. WhatsMerge takes any pile of those exports
and produces a single browsable archive:

- **Stitches partial exports.** Two exports of the same chat taken weeks apart
  become one continuous timeline with the overlap removed exactly once.
- **Keeps provenance.** Every message records which export it came from. A
  green dot marks messages confirmed by more than one export, and the details
  panel breaks down each source's contribution.
- **Looks like WhatsApp Web.** Nav rail, sidebar, bubbles with tails, date
  separators, per-sender colours in groups, voice notes with real waveforms,
  timestamps overlaid on photos, read-only composer, light and dark themes.
- **Embeds media.** Photos, video, voice notes and documents are inlined, so
  the single `.html` is the whole archive.
- **Searches everything.** Full-text search across all conversations, jumping
  straight to the matching message.

## Usage

```
python -m app [PATH ...] [options]
```

With no `PATH`, it reads `./exports` and writes `./output/archive.html`.
`PATH` can be `.zip` archives, `.txt` transcripts, or folders of either.

| Option | Effect |
| --- | --- |
| `-o, --output FILE` | Output HTML file. Default `output/archive.html` |
| `-t, --title TEXT` | Archive title |
| `--me NAME` | Your name as it appears in the exports. Auto-detected if omitted |
| `--media {inline,external,none}` | `inline` (default) gives one self-contained file; `external` copies media into `<output>_files/media`; `none` omits it |
| `--max-inline-mb N` | Files above this are not inlined. Default 16 |
| `--date-order {auto,dmy,mdy,ymd}` | How to read `03/04/2026`. Default `auto` |
| `--no-stitch` | Only join exports whose conversation name matches exactly |
| `--ticks {delivered,read,sent,none}` | Which delivery ticks to draw. Default `delivered` |
| `--theme {auto,light,dark}` | Initial colour theme |
| `--open` | Open the result in your browser when done |

### Examples

```powershell
.\merge.ps1                          # exports/ -> output/archive.html
.\merge.ps1 -Me "Musab Shaikh"       # say who owns the archive
.\merge.ps1 -External                # keep media beside the HTML

python -m app "Chat with Ahmed.zip" "Chat with Ahmed (2).zip" -o output/ahmed.html
python -m app -- --ticks none --theme dark
```

## Reading the result

| Control | Does |
| --- | --- |
| `/` | Focus search |
| `Esc` | Close the panel, or clear the search |
| Rail: chat icon | Conversation list |
| Rail: image icon | Every photo and video in the archive |
| Rail: chart icon | Archive-wide statistics |
| Rail: moon / printer | Cycle theme · print or save as PDF |
| Click the chat header | Conversation details, per-sender counts, sources |
| Click a photo | Lightbox; `←` `→` page through that chat's media |

A green dot beside a timestamp means the message was present in more than one
of your exports.

## What the exports do and don't contain

Worth knowing before you read anything into the output:

- **No read receipts.** Exports carry no delivery or read state. The grey `✓✓`
  is presentation only — it is not evidence a message was delivered. Use
  `--ticks none` if that matters for your use.
- **No profile photos.** Hence the default silhouette avatars, exactly as
  WhatsApp shows for a contact with no picture.
- **No timezone.** Timestamps are wall-clock in whatever zone the exporting
  phone was set to. WhatsMerge renders them exactly as exported, so the archive
  shows the same clock on any machine.
- **No reply threading.** WhatsApp does not export which message a reply quoted.
- **No reactions.** They are absent from the export format entirely.
- **"Media omitted".** An export made without media marks each attachment with
  a placeholder. If another export of the same chat has the real file,
  WhatsMerge pairs them up and keeps the real one.
- **Ambiguous dates.** A chat where every date has a day ≤ 12 cannot tell you
  whether it is `dd/mm` or `mm/dd`. WhatsMerge assumes `dd/mm` and warns. Pass
  `--date-order` to settle it.

## Format coverage

- iOS `[12/03/2026, 14:23:45] Name: message`, including the LRM markers and the
  narrow no-break space iOS 17+ puts before AM/PM
- Android `12/03/2026, 14:23 - Name: message`
- Day/month, month/day and ISO date orders, inferred per file
- 12- and 24-hour clocks; Arabic-Indic digits and `ص` / `م` meridiem markers
- Multi-line messages, captioned attachments, deleted and edited messages,
  missed calls, locations, polls, contact cards, group notifications
- `*bold*`, `_italic_`, `~strike~` and `` `monospace` `` rendered as WhatsApp does

## Development

```powershell
python tools/make_sample.py sample     # synthetic exports; all data fictional
python -m app sample -o output/demo.html --open
python -m unittest discover -s tests
```

`tools/make_sample.py` builds three exports that exercise the awkward cases:
two overlapping iOS exports of one chat (with the same photo carrying different
sequence numbers in each) plus an Android group export.

### Layout

| File | Responsibility |
| --- | --- |
| `app/ingest.py` | Unpack zips, find transcripts, recover mis-decoded filenames |
| `app/parser.py` | Transcript → messages; format, locale and date-order sniffing |
| `app/merge.py` | Chat identity, de-duplication, owner detection |
| `app/media.py` | Inlining, copying, size limits |
| `app/render.py` | Payload assembly and HTML output |
| `app/assets/` | The viewer: `shell.html`, `app.css`, `app.js` |

The viewer renders client-side from a JSON payload embedded in the page. That
is what lets search, chat switching and the media lightbox work inside a single
file with no server.

### Verifying UI changes

There is no Node in this project. To check the viewer actually renders, drive a
headless browser and look at the result:

```powershell
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$u = "file:///" + ((Resolve-Path output\archive.html).Path -replace '\\','/')
Start-Process $edge -ArgumentList @("--headless=new","--disable-gpu","--hide-scrollbars",
  "--virtual-time-budget=7000","--window-size=1400,900",
  "--screenshot=`"$PWD\output\preview.png`"","`"$u`"") -Wait -NoNewWindow
```

## Privacy

The output opens from `file://` and makes no network requests. Everything —
messages, photos, voice notes — lives inside the HTML you generated. Sharing
that file shares the entire conversation, so treat it exactly like the exports
it was built from.

`exports/` and `output/` are both gitignored. Chat data never reaches the repo.

## Licence

MIT.
