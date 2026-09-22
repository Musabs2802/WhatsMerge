"""Parser tests. All sample conversations below are fictional."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ingest import ExportUnit, clean_title  # noqa: E402
from app.models import KIND_DELETED, KIND_MEDIA, KIND_SYSTEM  # noqa: E402
from app.parser import TimestampReader, parse_transcript  # noqa: E402

LRM = "‎"


def parse(text: str, name: str = "WhatsApp Chat with Ahmed Al-Rashid.txt", **kw):
    """Parse an in-memory transcript through the real file path."""
    import tempfile

    tmp = Path(tempfile.mkdtemp()) / name
    tmp.write_text(text, encoding="utf-8")
    unit = ExportUnit(label=name, transcript=tmp, media_root=tmp.parent,
                      hint_title=clean_title(name))
    return parse_transcript(unit, **kw)


class TimestampTests(unittest.TestCase):
    def test_day_month_inferred_from_a_day_above_twelve(self):
        reader = TimestampReader()
        reader.sniff(["25/03/2026, 14:00:00", "03/04/2026, 09:00:00"])
        self.assertEqual(reader.order, "dmy")
        self.assertEqual(reader.parse("03/04/2026, 09:00:00"), datetime(2026, 4, 3, 9, 0))

    def test_month_day_inferred_from_a_month_above_twelve(self):
        reader = TimestampReader()
        reader.sniff(["03/25/2026, 14:00:00"])
        self.assertEqual(reader.order, "mdy")
        self.assertEqual(reader.parse("03/04/2026, 09:00:00"), datetime(2026, 3, 4, 9, 0))

    def test_iso_dates(self):
        reader = TimestampReader()
        reader.sniff(["2026-03-12, 14:00:00"])
        self.assertEqual(reader.parse("2026-03-12, 14:00:00"), datetime(2026, 3, 12, 14, 0))

    def test_twelve_hour_clock(self):
        reader = TimestampReader("dmy")
        self.assertEqual(reader.parse("12/03/2026, 2:23:45 PM"), datetime(2026, 3, 12, 14, 23, 45))
        self.assertEqual(reader.parse("12/03/2026, 12:05 AM"), datetime(2026, 3, 12, 0, 5))
        self.assertEqual(reader.parse("12/03/2026, 12:05 PM"), datetime(2026, 3, 12, 12, 5))

    def test_narrow_nbsp_before_meridiem(self):
        reader = TimestampReader("dmy")
        self.assertEqual(reader.parse("12/03/2026, 2:23:45 PM"), datetime(2026, 3, 12, 14, 23, 45))

    def test_arabic_indic_digits_and_meridiem(self):
        reader = TimestampReader("dmy")
        self.assertEqual(
            reader.parse("١٢/٠٣/٢٠٢٦, ٢:٣٠ م"),
            datetime(2026, 3, 12, 14, 30),
        )

    def test_two_digit_year(self):
        reader = TimestampReader("dmy")
        self.assertEqual(reader.parse("12/03/26, 08:00"), datetime(2026, 3, 12, 8, 0))


class IosFormatTests(unittest.TestCase):
    def test_basic_message(self):
        chat = parse(f"[12/03/2026, 14:23:45] Ahmed Al-Rashid: Container is cleared\n")
        self.assertEqual(len(chat.messages), 1)
        msg = chat.messages[0]
        self.assertEqual(msg.sender, "Ahmed Al-Rashid")
        self.assertEqual(msg.text, "Container is cleared")
        self.assertTrue(msg.has_seconds)

    def test_system_line_has_no_sender(self):
        chat = parse(
            f"[12/03/2026, 14:23:45] {LRM}Messages and calls are end-to-end encrypted.\n"
            f"[12/03/2026, 14:24:00] Ahmed Al-Rashid: hi\n"
        )
        self.assertIsNone(chat.messages[0].sender)
        self.assertEqual(chat.messages[0].kind, KIND_SYSTEM)

    def test_multiline_body(self):
        chat = parse(
            "[12/03/2026, 14:23:45] Musab Shaikh: Two points:\n"
            "1. Certificate attached\n"
            "2. ETA moved\n"
        )
        self.assertEqual(len(chat.messages), 1)
        self.assertEqual(chat.messages[0].text, "Two points:\n1. Certificate attached\n2. ETA moved")

    def test_attachment_with_caption(self):
        chat = parse(
            f"[12/03/2026, 14:23:45] Musab Shaikh: {LRM}<attached: 00000020-PHOTO-2026-03-12.jpg>\n"
            "Seal number SL-4471822\n"
        )
        msg = chat.messages[0]
        self.assertEqual(msg.kind, KIND_MEDIA)
        self.assertEqual(msg.attachment.filename, "00000020-PHOTO-2026-03-12.jpg")
        self.assertEqual(msg.attachment.kind, "image")
        self.assertEqual(msg.text, "Seal number SL-4471822")

    def test_deleted_and_edited(self):
        chat = parse(
            f"[12/03/2026, 14:23:45] Musab Shaikh: {LRM}This message was deleted\n"
            f"[12/03/2026, 14:24:00] Musab Shaikh: Booking confirmed{LRM}<This message was edited>\n"
        )
        # A deleted marker is LRM-prefixed like a notification, but it belongs
        # to a sender, so the sender must survive.
        self.assertEqual(chat.messages[1].text, "Booking confirmed")
        self.assertTrue(chat.messages[1].edited)

    def test_colon_in_system_line_does_not_create_a_sender(self):
        chat = parse(
            "[12/03/2026, 10:00:00] Fatima Nasser: first\n"
            "[12/03/2026, 10:01:00] Fatima Nasser: second\n"
            '[12/03/2026, 10:02:00] Fatima Nasser changed the subject to "Q1: close"\n'
        )
        self.assertEqual({m.sender for m in chat.messages}, {"Fatima Nasser", None})


class AndroidFormatTests(unittest.TestCase):
    def test_basic_message(self):
        chat = parse("12/03/2026, 14:23 - Ahmed Al-Rashid: Container is cleared\n")
        msg = chat.messages[0]
        self.assertEqual(msg.sender, "Ahmed Al-Rashid")
        self.assertFalse(msg.has_seconds)

    def test_file_attached(self):
        chat = parse(
            "12/03/2026, 14:23 - Paulo626: LOG-2026-03-08.pdf (file attached)\n"
            "Cold chain logs.\n"
        )
        msg = chat.messages[0]
        self.assertEqual(msg.attachment.filename, "LOG-2026-03-08.pdf")
        self.assertEqual(msg.attachment.kind, "document")
        self.assertEqual(msg.text, "Cold chain logs.")

    def test_sentence_ending_in_parentheses_is_not_an_attachment(self):
        chat = parse("12/03/2026, 14:23 - Paulo626: Please review spec.pdf (the newest one)\n")
        self.assertIsNone(chat.messages[0].attachment)

    def test_media_omitted(self):
        chat = parse("12/03/2026, 14:23 - Fatima Nasser: <Media omitted>\n")
        msg = chat.messages[0]
        self.assertEqual(msg.kind, KIND_MEDIA)
        self.assertTrue(msg.attachment.omitted)

    def test_voice_note_classified_from_filename(self):
        chat = parse("12/03/2026, 14:23 - Paulo626: PTT-20260312-WA0001.opus (file attached)\n")
        self.assertEqual(chat.messages[0].attachment.kind, "voice")


class TitleTests(unittest.TestCase):
    def test_clean_title_strips_prefix_and_counter(self):
        self.assertEqual(clean_title("WhatsApp Chat with Ahmed Al-Rashid.zip"), "Ahmed Al-Rashid")
        self.assertEqual(clean_title("WhatsApp Chat with Ahmed Al-Rashid (2).zip"), "Ahmed Al-Rashid")
        self.assertEqual(clean_title("Conversa do WhatsApp com Paulo626.txt"), "Paulo626")


if __name__ == "__main__":
    unittest.main()
