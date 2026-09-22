"""Generate synthetic WhatsApp exports for testing WhatsMerge.

Everything produced here is fictional: invented names, invented companies,
invented shipment numbers. It exercises the awkward parts of the real
format — two overlapping exports of one chat, an iOS export and an Android
one, media that is present, media that is omitted, deleted and edited
messages, multi-line bodies, and a group with system notifications.

    python tools/make_sample.py sample/
"""

from __future__ import annotations

import math
import struct
import sys
import wave
import zipfile
import zlib
from pathlib import Path

LRM = "‎"


# --------------------------------------------------------------- media bits


def png(width: int, height: int, rgb: tuple[int, int, int], stripe: tuple[int, int, int]) -> bytes:
    """A minimal two-tone PNG, built without any imaging library."""
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter type 0
        for x in range(width):
            band = ((x // 16) + (y // 16)) % 2
            r, g, b = rgb if band else stripe
            rows += bytes((r, g, b))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )


def wav(path: Path, seconds: float = 1.6, freq: float = 420.0) -> None:
    rate = 8000
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        frames = bytearray()
        for i in range(int(rate * seconds)):
            decay = 1 - i / (rate * seconds)
            value = int(12000 * decay * math.sin(2 * math.pi * freq * i / rate))
            frames += struct.pack("<h", value)
        fh.writeframes(bytes(frames))


def pdf(title: str, lines: list[str]) -> bytes:
    """A one-page PDF, hand-assembled."""
    body = "BT /F1 13 Tf 60 760 Td 16 TL\n"
    body += f"({title}) Tj T*\nT*\n"
    for line in lines:
        safe = line.replace("\\", "").replace("(", "").replace(")", "")
        body += f"({safe}) Tj T*\n"
    body += "ET"
    stream = body.encode("latin-1", "replace")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


# ------------------------------------------------------------- transcripts


def ios(stamp: str, sender: str | None, body: str) -> str:
    if sender is None:
        return f"[{stamp}] {LRM}{body}"
    return f"[{stamp}] {sender}: {body}"


def android(stamp: str, sender: str | None, body: str) -> str:
    if sender is None:
        return f"{stamp} - {body}"
    return f"{stamp} - {sender}: {body}"


ME = "Musab Shaikh"
AHMED = "Ahmed Al-Rashid"
FATIMA = "Fatima Nasser"
PAULO = "Paulo626"


def chat_ahmed_part1() -> tuple[str, dict[str, bytes]]:
    """iOS export covering 10–12 March."""
    L = []
    L.append(ios("10/03/2026, 08:12:04", None,
                 "Messages and calls are end-to-end encrypted. No one outside of this chat, "
                 "not even WhatsApp, can read or listen to them."))
    L.append(ios("10/03/2026, 08:12:05", AHMED, "Good morning Musab. Any update on the Jeddah container?"))
    L.append(ios("10/03/2026, 08:14:31", ME, "Morning Ahmed. Loading finished last night."))
    L.append(ios("10/03/2026, 08:14:58", ME,
                 "Two points:\n1. Halal certificate is attached below\n2. Vessel ETA moved to 21/03"))
    L.append(ios("10/03/2026, 08:15:10", ME, f"{LRM}<attached: 00000019-DOCUMENT-2026-03-10-08-15-10.pdf>"))
    L.append(ios("10/03/2026, 08:19:02", AHMED, "Received. I will forward to our customs broker."))
    L.append(ios("10/03/2026, 09:02:44", AHMED, "Can you send a photo of the seal?"))
    # Captioned photo: the marker on the first line, caption underneath.
    L.append(ios("10/03/2026, 09:31:17", ME,
                 f"{LRM}<attached: 00000020-PHOTO-2026-03-10-09-31-17.png>\nSeal number SL-4471822"))
    L.append(ios("10/03/2026, 09:40:05", AHMED, "Perfect 👍"))
    L.append(ios("11/03/2026, 14:05:21", AHMED, "One more thing — the *labelling* needs the Arabic net weight on the front panel."))
    L.append(ios("11/03/2026, 14:22:03", ME, "Understood. Our packaging spec already has it, I'll confirm with Quality today."))
    L.append(ios("11/03/2026, 16:48:12", ME, f"{LRM}<attached: 00000021-AUDIO-2026-03-11-16-48-12.wav>"))
    L.append(ios("11/03/2026, 16:52:40", AHMED, "Thanks, clear."))
    L.append(ios("12/03/2026, 07:30:00", AHMED, "Sending the revised PO now."))
    L.append(ios("12/03/2026, 07:31:15", ME, "Noted, thanks."))

    media = {
        "00000019-DOCUMENT-2026-03-10-08-15-10.pdf": pdf(
            "Halal Certificate - FICTIONAL SAMPLE",
            [
                "Certificate no.: HAL-2026-000318 (example only)",
                "Product: Frozen whole chicken, 1100 g",
                "Consignment: SL-4471822",
                "Issued for testing WhatsMerge. Not a real document.",
            ],
        ),
        "00000020-PHOTO-2026-03-10-09-31-17.png": png(320, 200, (0, 120, 90), (240, 240, 235)),
        "00000021-AUDIO-2026-03-11-16-48-12.wav": b"",  # filled in by the caller
    }
    return "\n".join(L) + "\n", media


def chat_ahmed_part2() -> tuple[str, dict[str, bytes]]:
    """A second iOS export of the SAME chat: overlaps 11–12 March, then
    continues. Media carries different sequence numbers, exactly as a
    real re-export would."""
    L = []
    L.append(ios("11/03/2026, 14:05:21", AHMED, "One more thing — the *labelling* needs the Arabic net weight on the front panel."))
    L.append(ios("11/03/2026, 14:22:03", ME, "Understood. Our packaging spec already has it, I'll confirm with Quality today."))
    L.append(ios("11/03/2026, 16:48:12", ME, f"{LRM}<attached: 00000004-AUDIO-2026-03-11-16-48-12.wav>"))
    L.append(ios("11/03/2026, 16:52:40", AHMED, "Thanks, clear."))
    L.append(ios("12/03/2026, 07:30:00", AHMED, "Sending the revised PO now."))
    L.append(ios("12/03/2026, 07:31:15", ME, "Noted, thanks."))
    L.append(ios("12/03/2026, 11:02:38", AHMED, f"{LRM}<attached: 00000005-PHOTO-2026-03-12-11-02-38.png>"))
    L.append(ios("12/03/2026, 11:03:02", AHMED, "PO 88231-B, quantity unchanged."))
    L.append(ios("12/03/2026, 11:20:44", ME, "Received. I'll confirm the booking this afternoon. ~ignore the earlier ETA~"))
    L.append(ios("13/03/2026, 09:15:00", ME, f"{LRM}This message was deleted"))
    L.append(ios("13/03/2026, 09:16:20", ME, "Sorry, wrong chat."))
    L.append(ios("13/03/2026, 18:04:11", AHMED, "No problem. See the tracking here: https://example.com/track/SL-4471822"))
    L.append(ios("14/03/2026, 10:00:00", ME, "Booking confirmed, vessel MV Example Star.‎<This message was edited>"))
    L.append(ios("14/03/2026, 10:02:13", AHMED, "🎉"))
    L.append(ios("15/03/2026, 08:00:00", AHMED, f"{LRM}image omitted"))
    L.append(ios("15/03/2026, 08:41:09", ME, "Got it, thanks Ahmed."))

    media = {
        "00000004-AUDIO-2026-03-11-16-48-12.wav": b"",
        "00000005-PHOTO-2026-03-12-11-02-38.png": png(300, 220, (30, 60, 130), (250, 245, 230)),
    }
    return "\n".join(L) + "\n", media


def chat_group() -> tuple[str, dict[str, bytes]]:
    """Android export of a group, with system notifications."""
    L = []
    L.append(android("08/03/2026, 07:55", None,
                     "Messages and calls are end-to-end encrypted. Tap to learn more."))
    L.append(android("08/03/2026, 07:55", None, f"{FATIMA} created group \"Gulf Shipments Q1\""))
    L.append(android("08/03/2026, 07:56", None, f"{FATIMA} added you"))
    L.append(android("08/03/2026, 08:02", FATIMA, "Morning all. Status round for this week's three containers please."))
    L.append(android("08/03/2026, 08:11", ME, "Seara side: two loaded, one pending vet certificate."))
    L.append(android("08/03/2026, 08:14", AHMED, "Riyadh clearance is fine for both loaded units."))
    L.append(android("08/03/2026, 08:20", PAULO,
                     "LOG-2026-03-08.pdf (file attached)\nCold chain logs for EXMP-2026-003."))
    L.append(android("08/03/2026, 08:44", FATIMA, "Thanks Paulo."))
    L.append(android("08/03/2026, 08:45", FATIMA, "<Media omitted>"))
    L.append(android("09/03/2026, 12:30", None, f"{FATIMA} changed the subject to \"Gulf Shipments: Q1 close\""))
    L.append(android("09/03/2026, 12:31", FATIMA, "Renamed for clarity."))
    L.append(android("10/03/2026, 06:40", AHMED, "Question on the labelling rule — is the Arabic net weight mandatory on the front panel for all SKUs?"))
    L.append(android("10/03/2026, 07:02", ME,
                     "For the SKUs in this shipment yes.\nI'll circulate the packaging spec sheet."))
    L.append(android("10/03/2026, 07:05", ME, "IMG-20260310-WA0007.png (file attached)"))
    L.append(android("11/03/2026, 19:22", PAULO, "Missed voice call"))
    L.append(android("12/03/2026, 09:10", FATIMA, "Closing the loop: all three cleared. 🙏"))
    L.append(android("12/03/2026, 09:12", None, f"{PAULO} left"))

    media = {
        "LOG-2026-03-08.pdf": pdf(
            "Cold Chain Log - FICTIONAL SAMPLE",
            [
                "Container: EXMP-2026-003 (example only)",
                "Set point: -18 C",
                "Readings every 4 h, 08/03/2026",
                "Generated to test WhatsMerge. Not a real record.",
            ],
        ),
        "IMG-20260310-WA0007.png": png(280, 180, (150, 40, 60), (245, 240, 235)),
    }
    return "\n".join(L) + "\n", media


# ------------------------------------------------------------------- build


def write_zip(path: Path, transcript_name: str, transcript: str, media: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(transcript_name, transcript)
        for name, payload in media.items():
            zf.writestr(name, payload)


def main(dest: str = "sample") -> int:
    out = Path(dest)
    out.mkdir(parents=True, exist_ok=True)

    tmp_wav = out / "_tone.wav"
    wav(tmp_wav)
    tone = tmp_wav.read_bytes()
    tmp_wav.unlink()

    t1, m1 = chat_ahmed_part1()
    m1["00000021-AUDIO-2026-03-11-16-48-12.wav"] = tone
    write_zip(out / f"WhatsApp Chat with {AHMED}.zip", "_chat.txt", t1, m1)

    t2, m2 = chat_ahmed_part2()
    m2["00000004-AUDIO-2026-03-11-16-48-12.wav"] = tone
    write_zip(out / f"WhatsApp Chat with {AHMED} (2).zip", "_chat.txt", t2, m2)

    t3, m3 = chat_group()
    write_zip(
        out / "WhatsApp Chat with Gulf Shipments Q1.zip",
        "WhatsApp Chat with Gulf Shipments Q1.txt",
        t3,
        m3,
    )

    print(f"Wrote 3 sample exports to {out.resolve()}")
    print("All names, numbers and documents in them are fictional.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
