"""Stitching, de-duplication and owner detection. Names here are fictional."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Attachment, KIND_MEDIA, Message, ParsedChat  # noqa: E402
from app.merge import build_chats, dedup, detect_owner, group_exports  # noqa: E402
from app.render import build_payload, render_html  # noqa: E402

AHMED = "Ahmed Al-Rashid"
ME = "Musab Shaikh"
FATIMA = "Fatima Nasser"


def msg(day, hour, minute, sender, text, source="a.zip", second=0, attachment=None):
    return Message(
        ts=datetime(2026, 3, day, hour, minute, second),
        sender=sender,
        text=text,
        kind=KIND_MEDIA if attachment else "text",
        attachment=attachment,
        has_seconds=bool(second),
        sources={source},
    )


class DedupTests(unittest.TestCase):
    def test_identical_messages_collapse_and_keep_both_sources(self):
        a = msg(12, 9, 0, AHMED, "Container cleared", source="a.zip")
        b = msg(12, 9, 0, AHMED, "Container cleared", source="b.zip")
        out = dedup([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].sources, {"a.zip", "b.zip"})

    def test_seconds_precision_does_not_break_cross_platform_matching(self):
        ios = msg(12, 9, 0, AHMED, "Container cleared", source="ios.zip", second=43)
        android = msg(12, 9, 0, AHMED, "Container cleared", source="android.zip")
        out = dedup([ios, android])
        self.assertEqual(len(out), 1)
        # The richer record — the one that knows the seconds — survives.
        self.assertTrue(out[0].has_seconds)

    def test_same_photo_re_exported_under_a_new_sequence_number(self):
        first = msg(12, 9, 0, ME, "", source="a.zip",
                    attachment=Attachment("00000019-PHOTO-2026-03-12.jpg", "image",
                                          "image/jpeg", content_hash="abc123"))
        second = msg(12, 9, 0, ME, "", source="b.zip",
                     attachment=Attachment("00000004-PHOTO-2026-03-12.jpg", "image",
                                           "image/jpeg", content_hash="abc123"))
        self.assertEqual(len(dedup([first, second])), 1)

    def test_filename_matching_when_content_hash_is_unavailable(self):
        first = msg(12, 9, 0, ME, "", source="a.zip",
                    attachment=Attachment("00000019-PHOTO-2026-03-12.jpg", "image", "image/jpeg"))
        second = msg(12, 9, 0, ME, "", source="b.zip",
                     attachment=Attachment("00000004-PHOTO-2026-03-12.jpg", "image", "image/jpeg"))
        self.assertEqual(len(dedup([first, second])), 1)

    def test_placeholder_gives_way_to_the_resolved_attachment(self):
        omitted = msg(12, 9, 0, ME, "image omitted", source="a.zip",
                      attachment=Attachment("", "missing", "", omitted=True))
        real = msg(12, 9, 0, ME, "", source="b.zip",
                   attachment=Attachment("IMG-20260312-WA0001.jpg", "image", "image/jpeg"))
        real.attachment.path = "/tmp/IMG-20260312-WA0001.jpg"
        out = dedup([omitted, real])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].attachment.filename, "IMG-20260312-WA0001.jpg")
        self.assertEqual(out[0].sources, {"a.zip", "b.zip"})

    def test_distinct_messages_in_the_same_minute_are_kept(self):
        a = msg(12, 9, 0, AHMED, "one")
        b = msg(12, 9, 0, AHMED, "two")
        self.assertEqual(len(dedup([a, b])), 2)


class StitchTests(unittest.TestCase):
    def _overlapping_pair(self):
        shared = [
            ("Morning, any update?", AHMED),
            ("Loading finished last night.", ME),
            ("Received, thanks.", AHMED),
        ]
        first = ParsedChat(title=AHMED, source="part1.zip", messages=[
            msg(10, 8, i, who, text, source="part1.zip") for i, (text, who) in enumerate(shared)
        ])
        second = ParsedChat(title=AHMED, source="part2.zip", messages=[
            msg(10, 8, i, who, text, source="part2.zip") for i, (text, who) in enumerate(shared)
        ] + [msg(11, 9, 0, AHMED, "Sending the revised PO.", source="part2.zip")])
        return [first, second]

    def test_two_exports_of_one_chat_become_one_conversation(self):
        chats, _ = build_chats(self._overlapping_pair())
        self.assertEqual(len(chats), 1)
        self.assertEqual(len(chats[0].messages), 4)
        self.assertEqual(sorted(chats[0].sources), ["part1.zip", "part2.zip"])

    def test_no_stitch_keeps_differently_named_exports_apart(self):
        pair = self._overlapping_pair()
        pair[1].title = "Ahmed"  # a differently-named re-export
        chats, _ = build_chats(pair, stitch=False)
        self.assertEqual(len(chats), 2)

    def test_matching_participants_stitch_a_renamed_one_to_one_export(self):
        pair = self._overlapping_pair()
        pair[1].title = "Ahmed"
        self.assertEqual(len(group_exports(pair)), 1)

    def test_different_conversations_stay_separate(self):
        a = ParsedChat(title=AHMED, source="a.zip",
                       messages=[msg(10, 8, 0, AHMED, "hello", source="a.zip"),
                                 msg(10, 8, 1, ME, "hi", source="a.zip")])
        b = ParsedChat(title=FATIMA, source="b.zip",
                       messages=[msg(10, 8, 0, FATIMA, "morning", source="b.zip"),
                                 msg(10, 8, 1, ME, "morning", source="b.zip")])
        chats, _ = build_chats([a, b])
        self.assertEqual(len(chats), 2)

    def test_messages_end_up_in_chronological_order(self):
        chats, _ = build_chats(self._overlapping_pair())
        stamps = [m.ts for m in chats[0].messages]
        self.assertEqual(stamps, sorted(stamps))


class OwnerTests(unittest.TestCase):
    def _two_chats(self):
        a = ParsedChat(title=AHMED, source="a.zip",
                       messages=[msg(10, 8, 0, AHMED, "hello", source="a.zip"),
                                 msg(10, 8, 1, ME, "hi", source="a.zip")])
        b = ParsedChat(title=FATIMA, source="b.zip",
                       messages=[msg(10, 9, 0, FATIMA, "morning", source="b.zip"),
                                 msg(10, 9, 1, ME, "morning", source="b.zip")])
        return [a, b]

    def test_owner_is_the_participant_common_to_every_chat(self):
        chats, owner = build_chats(self._two_chats())
        self.assertEqual(owner, ME)

    def test_single_chat_falls_back_to_the_title_naming_the_other_party(self):
        a = ParsedChat(title=AHMED, source="a.zip",
                       messages=[msg(10, 8, 0, AHMED, "hello", source="a.zip"),
                                 msg(10, 8, 1, ME, "hi", source="a.zip")])
        _, owner = build_chats([a])
        self.assertEqual(owner, ME)

    def test_override_wins_and_tolerates_a_near_miss(self):
        chats = [ParsedChat(title=AHMED, source="a.zip",
                            messages=[msg(10, 8, 0, AHMED, "hello", source="a.zip"),
                                      msg(10, 8, 1, ME, "hi", source="a.zip")])]
        _, owner = build_chats(chats, owner_override="musab shaikh")
        self.assertEqual(owner, ME)

    def test_owner_messages_are_marked_outgoing(self):
        chats, _ = build_chats(self._two_chats())
        mine = [m for c in chats for m in c.messages if m.sender == ME]
        self.assertTrue(mine and all(m.outgoing for m in mine))
        theirs = [m for c in chats for m in c.messages if m.sender == AHMED]
        self.assertTrue(theirs and not any(m.outgoing for m in theirs))

    def test_detect_owner_returns_none_for_an_empty_archive(self):
        self.assertIsNone(detect_owner([]))


class RenderTests(unittest.TestCase):
    def _payload(self):
        a = ParsedChat(title=AHMED, source="a.zip",
                       messages=[msg(10, 8, 0, AHMED, "hello"), msg(10, 8, 1, ME, "hi")])
        chats, owner = build_chats([a])
        return build_payload(chats, owner=owner, title="Test archive")

    def test_payload_shape(self):
        payload = self._payload()
        self.assertEqual(payload["owner"], ME)
        chat = payload["chats"][0]
        self.assertEqual(len(chat["msgs"]), 2)
        self.assertIn("w", chat["msgs"][0])

    def test_script_terminator_in_a_message_cannot_break_out(self):
        a = ParsedChat(title=AHMED, source="a.zip", messages=[
            msg(10, 8, 0, AHMED, "</script><script>alert(1)</script>"),
            msg(10, 8, 1, ME, "<!-- comment --> & <b>bold</b>"),
        ])
        chats, owner = build_chats([a])
        html = render_html(build_payload(chats, owner=owner, title="x"))
        body = html.split('id="payload">', 1)[1].split("</script>", 1)[0]
        self.assertNotIn("</script>", body)
        self.assertNotIn("<!--", body)
        # And it still round-trips to the original text.
        data = json.loads(body.replace("<\\/", "</").replace("<\\!--", "<!--"))
        self.assertEqual(data["chats"][0]["msgs"][0]["b"], "</script><script>alert(1)</script>")

    def test_timestamps_are_timezone_independent(self):
        payload = self._payload()
        # 10/03/2026 08:00 UTC
        self.assertEqual(payload["chats"][0]["msgs"][0]["t"], 1773129600)


if __name__ == "__main__":
    unittest.main()
