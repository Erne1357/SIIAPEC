# tests/utils/test_uuid7.py
"""
Layout and ordering tests for the hand-rolled UUIDv7 generator.

The algorithm is written out by hand in app/utils/uuid7.py because the stdlib
`uuid.uuid7()` only exists from Python 3.14 and this project is pinned to 3.11.
Hand-rolled bit packing is exactly the kind of code that is silently wrong, so
every field of RFC 9562 §5.7 is asserted here against the raw 128-bit value —
not via `UUID.version`, which would only re-read the same nibble the generator
wrote, but against the documented offsets.

No database and no application context: the generator is pure stdlib.
"""

import time
import unittest
import uuid

from app.utils.uuid7 import parse_uuid, uuid7, uuid7_str


class UUID7LayoutTests(unittest.TestCase):
    """RFC 9562 §5.7 field-by-field."""

    def test_version_nibble_is_seven(self):
        for _ in range(200):
            value = uuid7()
            # Bits 76..79 (counting from the least significant) are `ver`.
            self.assertEqual((value.int >> 76) & 0xF, 0b0111)
            # And the stdlib agrees with our own bit arithmetic.
            self.assertEqual(value.version, 7)

    def test_variant_bits_are_rfc_9562(self):
        for _ in range(200):
            value = uuid7()
            # Bits 62..63 are `var`, and must be 0b10.
            self.assertEqual((value.int >> 62) & 0b11, 0b10)
            self.assertEqual(value.variant, uuid.RFC_4122)

    def test_timestamp_field_is_unix_milliseconds(self):
        before = time.time_ns() // 1_000_000
        value = uuid7()
        after = time.time_ns() // 1_000_000

        # The leading 48 bits are big-endian Unix epoch milliseconds, which is
        # the same as reading the first six bytes big-endian.
        ts_from_int = value.int >> 80
        ts_from_bytes = int.from_bytes(value.bytes[:6], 'big')

        self.assertEqual(ts_from_int, ts_from_bytes)
        self.assertGreaterEqual(ts_from_int, before)
        self.assertLessEqual(ts_from_int, after)

    def test_is_128_bits(self):
        value = uuid7()
        self.assertEqual(len(value.bytes), 16)
        self.assertLess(value.int, 1 << 128)


class UUID7OrderingTests(unittest.TestCase):
    """Time ordering is the whole reason v7 was chosen over v4."""

    def test_two_values_a_millisecond_apart_sort_in_time_order(self):
        first = uuid7()
        time.sleep(0.002)          # comfortably more than one millisecond
        second = uuid7()

        self.assertLess(first, second)
        # The database compares the stored uuid as bytes, so assert that too.
        self.assertLess(first.bytes, second.bytes)
        self.assertLess(str(first), str(second))
        # The ordering must come from the timestamp, not from luck in rand_b.
        self.assertLess(first.int >> 80, second.int >> 80)

    def test_burst_within_one_millisecond_is_strictly_increasing(self):
        # Every value in a same-millisecond burst must still be distinct and
        # ordered — that is what the rand_a counter is for.
        values = [uuid7() for _ in range(5000)]
        self.assertEqual(values, sorted(values))
        self.assertEqual(len(set(values)), len(values))

    def test_values_are_unique(self):
        values = {uuid7() for _ in range(10000)}
        self.assertEqual(len(values), 10000)


class ParseUUIDTests(unittest.TestCase):
    """`parse_uuid` must never raise — a route turns None into a clean 404."""

    def test_round_trips_canonical_form(self):
        value = uuid7()
        self.assertEqual(parse_uuid(str(value)), value)

    def test_accepts_a_uuid_instance_unchanged(self):
        value = uuid7()
        self.assertIs(parse_uuid(value), value)

    def test_returns_none_for_malformed_input(self):
        for bad in (
            None,
            '',
            '   ',
            '1',
            '42',
            'not-a-uuid',
            'zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz',
            '0192f0a4-3d7e-7000-8000',                    # too short
            '0192f0a4-3d7e-7000-8000-0123456789abcdef',   # too long
            '../../etc/passwd',
            "1 OR 1=1",
            123,
            1.5,
            [],
            {},
            object(),
        ):
            with self.subTest(bad=bad):
                self.assertIsNone(parse_uuid(bad))

    def test_uuid7_str_is_the_canonical_hyphenated_form(self):
        text = uuid7_str()
        self.assertEqual(len(text), 36)
        self.assertEqual(text.count('-'), 4)
        self.assertEqual(str(parse_uuid(text)), text)
        self.assertEqual(parse_uuid(text).version, 7)


if __name__ == '__main__':
    unittest.main()
