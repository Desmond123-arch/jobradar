"""Time zones: your zone, your range, and reading zone wording in postings."""

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import timezones as tz  # noqa: E402


def verdict(location, description="", me=0, hours=3):
    return tz.Zone(me, hours).check(location, description)


class YourZoneTests(unittest.TestCase):
    def test_formatting(self):
        self.assertEqual([tz.fmt(x) for x in (-8, 0, 1, 5.5, -3.5, 5.75)], ["UTC-8", "UTC+0", "UTC+1", "UTC+5.5", "UTC-3.5", "UTC+5.75"])

    def test_valid_settings(self):
        for ok in ("auto", "UTC+0", "UTC-8", "UTC+5.5", "UTC+14", "UTC-12"):
            self.assertTrue(tz.valid(ok), ok)
        for bad in ("", "UTC+15", "UTC-13", "Europe/Paris", "utc+1", "UTC1", "UTC+", None, 3):
            self.assertFalse(tz.valid(bad), bad)

    def test_every_dropdown_zone_is_a_valid_setting_and_round_trips(self):
        for offset, places in tz.ZONES:
            key = tz.key_for(offset)
            self.assertTrue(tz.valid(key), key)
            self.assertEqual(tz.resolve(key), offset)
            self.assertTrue(places)
        self.assertEqual(len({o for o, _ in tz.ZONES}), len(tz.ZONES))  # no duplicates

    def test_auto_follows_this_computer_standard_time(self):
        with mock.patch.object(time, "timezone", 0):
            self.assertEqual(tz.resolve("auto"), 0)
            self.assertEqual(tz.describe("auto"), "this computer (UTC+0)")
        with mock.patch.object(time, "timezone", 5 * 3600):  # seconds WEST of UTC, so UTC-5
            self.assertEqual(tz.resolve("auto"), -5)
        with mock.patch.object(time, "timezone", -int(5.5 * 3600)):
            self.assertEqual(tz.resolve("auto"), 5.5)

    def test_an_unreadable_computer_zone_falls_back_to_utc(self):
        self.assertEqual(tz.FALLBACK, 0)
        for bad in (10 ** 9, -10 ** 9, 99 * 3600):
            with mock.patch.object(time, "timezone", bad):
                self.assertEqual(tz.system_offset(), 0, bad)
        with mock.patch.object(time, "timezone", "oops"):
            self.assertEqual(tz.system_offset(), 0)
        with mock.patch.object(time, "timezone", 0):
            self.assertEqual(tz.resolve(None), 0)

    def test_the_dropdown_names_accra_at_utc_plus_0(self):
        label = dict(tz.ZONES)[0]
        self.assertTrue(label.startswith("Accra"), label)

    def test_normalize_what_people_type(self):
        for typed, stored in (("auto", "auto"), ("AUTO", "auto"), ("UTC+1", "UTC+1"), ("utc-8", "UTC-8"), ("+1", "UTC+1"),
                              ("5.5", "UTC+5.5"), ("GMT+2", "UTC+2"), ("UTC", None), ("UTC+20", None), ("Paris", None), ("", None)):
            self.assertEqual(tz.normalize(typed), stored, typed)

    def test_unreadable_settings_mean_this_computer(self):
        with mock.patch.object(time, "timezone", -3600):
            for junk in ("", None, "nonsense", "UTC+99"):
                self.assertEqual(tz.resolve(junk), 1, junk)

    def test_describe_names_places(self):
        self.assertIn("Lagos", tz.describe("UTC+1"))
        self.assertTrue(tz.describe("UTC+3.5").startswith("UTC+3.5"))

    def test_range_options_end_with_off_and_label_correctly(self):
        self.assertEqual(tz.RANGES[-1][0], tz.ANY_RANGE)
        self.assertEqual([tz.range_label(h) for h in (0, 3, 24)], ["same zone", "within 3h", "off"])

    def test_make_returns_nothing_when_the_range_is_wide_open(self):
        self.assertIsNone(tz.make("UTC+1", tz.ANY_RANGE))
        zone = tz.make("UTC+1", 2)
        self.assertEqual((zone.offset, zone.hours), (1, 2))


class AbbreviationTests(unittest.TestCase):
    def test_close_zones_fit_and_far_ones_do_not(self):
        for loc in ("Remote (CET)", "Remote (WAT)", "Remote (GMT)", "Remote (UTC)", "Remote (SAST)", "Remote (EET)", "Remote - GMT+1"):
            self.assertEqual(verdict(loc).status, "ok", loc)
        for loc in ("Remote (PST)", "Remote (EST hours)", "Remote (EDT)", "Remote (JST)", "Remote (AEST)", "Remote (NZST)"):
            self.assertEqual(verdict(loc).status, "mismatch", loc)

    def test_the_range_decides_where_the_line_is(self):
        self.assertEqual(verdict("Remote (CET)", hours=0).status, "mismatch")  # one hour away, same zone only
        self.assertEqual(verdict("Remote (CET)", hours=1).status, "ok")
        self.assertEqual(verdict("Remote (EAT)", hours=2).status, "mismatch")
        self.assertEqual(verdict("Remote (EAT)", hours=3).status, "ok")
        self.assertEqual(verdict("Remote (EST)", hours=12).status, "ok")

    def test_your_own_zone_decides_too(self):
        self.assertEqual(verdict("Remote (PST)", me=-8).status, "ok")
        self.assertEqual(verdict("Remote (CET)", me=-8).status, "mismatch")
        self.assertEqual(verdict("Remote (AEST)", me=9).status, "ok")

    def test_lowercase_words_are_not_zones(self):
        for text in ("Remote. Established company, best of luck, concatenate", "Remote - cat person, west coast of Africa", "Remote Est."):
            self.assertEqual(verdict(text).status, "", text)

    def test_names_that_mean_two_zones_are_never_guessed(self):
        for text in ("Remote (CST)", "Remote (IST)", "Remote - India hours"):
            self.assertEqual(verdict(text).status, "", text)

    def test_the_word_after_decides_not_the_letters_inside_a_word(self):
        self.assertEqual(verdict("Remote (CETUS)").status, "")  # not CET
        self.assertEqual(verdict("Remote (ESTONIA)").status, "")


class WordedZoneTests(unittest.TestCase):
    def test_us_zone_names(self):
        for loc in ("Remote (Pacific Time)", "Remote - Eastern Standard Time", "Working in Central time"):
            self.assertEqual(verdict(loc).status, "mismatch", loc)

    def test_regional_names(self):
        self.assertEqual(verdict("Remote - Central European Time").status, "ok")
        self.assertEqual(verdict("Remote - West Africa Time").status, "ok")
        self.assertEqual(verdict("Remote - East Africa Time").status, "ok")  # +3 is inside a 3 hour range
        self.assertEqual(verdict("Remote - East Africa Time", hours=2).status, "mismatch")
        self.assertEqual(verdict("Remote - British Summer Time").status, "ok")

    def test_regional_hours(self):
        self.assertEqual(verdict("Remote - European hours").status, "ok")
        self.assertEqual(verdict("Remote - UK business hours").status, "ok")
        self.assertEqual(verdict("Remote - East Coast hours").status, "mismatch")
        self.assertEqual(verdict("Remote - West Coast time").status, "mismatch")
        self.assertEqual(verdict("Remote - Australian hours").status, "mismatch")

    def test_us_hours_cover_the_whole_country_so_the_edges_count(self):
        self.assertEqual(verdict("Remote (US hours)", me=-4, hours=1).status, "ok")   # one hour from the east edge
        self.assertEqual(verdict("Remote (US hours)", me=-6, hours=0).status, "ok")   # inside
        self.assertEqual(verdict("Remote (US hours)", me=0, hours=3).status, "mismatch")
        self.assertEqual(verdict("Remote - North American hours", me=-9, hours=1).status, "ok")


class OffsetAndWindowTests(unittest.TestCase):
    def test_a_single_offset_is_a_zone_like_any_other(self):
        self.assertEqual(verdict("Remote UTC+2").status, "ok")
        self.assertEqual(verdict("Remote UTC+7").status, "mismatch")
        self.assertEqual(verdict("Remote GMT-5").status, "mismatch")

    def test_half_hour_and_quarter_hour_offsets(self):
        self.assertEqual(verdict("Remote UTC+5:30", me=5.5).status, "ok")
        self.assertEqual(verdict("Remote UTC+05:30", me=5.5, hours=0).status, "ok")
        self.assertEqual(verdict("Remote UTC+5:45", me=5.75, hours=0).status, "ok")

    def test_a_stated_range_is_an_eligibility_window_you_must_be_inside(self):
        self.assertEqual(verdict("Remote (UTC-5 to UTC+2)").status, "ok")
        self.assertEqual(verdict("Remote (UTC-8 to UTC-5)").status, "mismatch")
        self.assertEqual(verdict("Remote (UTC+1 to UTC+3)").status, "mismatch")  # you are just outside: strict
        self.assertEqual(verdict("Remote GMT-3 – GMT+3").status, "ok")
        self.assertEqual(verdict("Remote UTC-5 to +2").status, "ok")
        self.assertEqual(verdict("Remote UTC+3 to UTC-3").status, "ok")  # written backwards
        self.assertEqual(verdict("Remote UTC-8 / UTC-5").status, "mismatch")

    def test_within_n_hours_of_a_zone(self):
        self.assertEqual(verdict("Remote, within 3 hours of UTC").status, "ok")
        self.assertEqual(verdict("Remote, within 2 hours of CET").status, "ok")       # CET+1, window -1..+3, you are 0
        self.assertEqual(verdict("Remote, +/- 1 hour of EST").status, "mismatch")
        self.assertEqual(verdict("Remote, ±2 hrs of UTC-5").status, "mismatch")
        self.assertEqual(verdict("Remote, within 6 hours of UTC-5", me=0).status, "ok")

    def test_plus_or_minus_utc(self):
        self.assertEqual(verdict("Remote UTC±3").status, "ok")
        self.assertEqual(verdict("Remote UTC±3", me=5).status, "mismatch")

    def test_overlapping_matches_are_counted_once(self):
        found = tz.mentions("Remote within 3 hours of UTC+1")
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].kind, found[0].lo, found[0].hi), ("window", -2, 4))
        self.assertEqual(len(tz.mentions("Remote UTC-5 to UTC+2")), 1)


class DescriptionTests(unittest.TestCase):
    def test_sentences_about_hours_count(self):
        self.assertEqual(verdict("Remote", "Overlap with CET required.").status, "ok")
        self.assertEqual(verdict("Remote", "We work in US Pacific time zone only.").status, "mismatch")
        self.assertEqual(verdict("Remote", "Great team. Must be able to work EST hours. Good pay.").status, "mismatch")
        self.assertEqual(verdict("Remote", "Working hours: 9-5 Eastern Time.").status, "mismatch")

    def test_a_passing_mention_with_no_talk_of_hours_is_ignored(self):
        for text in ("Our team is in London (GMT) and New York (EST).", "Offices in Paris, EST and PST.", "Founded in the PST era."):
            self.assertEqual(verdict("Remote", text).status, "", text)

    def test_the_location_wins_over_the_description(self):
        self.assertEqual(verdict("Remote (CET)", "Overlap with PST hours required.").status, "ok")
        self.assertEqual(verdict("Remote (PST)", "Overlap with CET required.").status, "mismatch")

    def test_no_zone_wording_means_no_opinion(self):
        for desc in ("", "Great benefits and a friendly team.", "Established in 2010; best of luck."):
            self.assertEqual(verdict("Remote", desc).status, "", desc)
        self.assertEqual(verdict("", "").status, "")
        self.assertEqual(verdict(None, None).status, "")

    def test_a_long_run_on_sentence_is_not_mined(self):
        long = "hours " + "x " * 250 + "EST"
        self.assertEqual(verdict("Remote", long).status, "")


class AnyZoneTests(unittest.TestCase):
    def test_any_time_zone_always_fits(self):
        for text in ("Remote - any time zone", "Remote (all timezones)", "Time zone agnostic", "Remote, regardless of time zone"):
            self.assertEqual(verdict(text, me=-8).status, "ok", text)
        self.assertEqual(verdict("Remote", "Work from anywhere in the world.").status, "ok")

    def test_it_beats_a_zone_named_elsewhere_in_the_posting(self):
        self.assertEqual(verdict("Remote (PST)", "We are fully flexible: any time zone.").status, "ok")


class NotesTests(unittest.TestCase):
    def test_a_fit_says_which_zone_and_how_far(self):
        self.assertEqual(verdict("Remote (CET)").note, "time zone: CET (UTC+1), 1h from you")
        self.assertEqual(verdict("Remote (GMT)").note, "time zone: GMT (UTC+0), same as you")
        self.assertEqual(verdict("Remote (UTC-5 to UTC+2)").note, "time zone: UTC-5 to UTC+2")

    def test_a_mismatch_says_why(self):
        self.assertEqual(verdict("Remote (PST)").note, "time zone: PST (UTC-8) is 8h from you (you are UTC+0)")
        self.assertIn("wants UTC-8 to UTC-5", verdict("Remote (UTC-8 to UTC-5)").note)

    def test_the_nearest_of_several_zones_is_reported(self):
        self.assertIn("EST", verdict("Remote (PST or EST)").note)

    def test_any_one_matching_zone_is_enough(self):
        self.assertEqual(verdict("Remote (PST or CET)").status, "ok")
        self.assertEqual(verdict("Remote (PST or EST)").status, "mismatch")


class SwitchedOffTests(unittest.TestCase):
    def test_a_range_of_24_hours_judges_nothing(self):
        self.assertEqual(verdict("Remote (PST)", hours=tz.ANY_RANGE).status, "")
        self.assertEqual(verdict("Remote (PST)", hours=100).status, "")


if __name__ == "__main__":
    unittest.main()
