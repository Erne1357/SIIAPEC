# app/utils/uuid7.py
"""
UUIDv7 generation (RFC 9562 §5.7) — the single source of public identifiers.

Why hand-rolled
---------------
`uuid.uuid7()` landed in the Python standard library in 3.14. This project is
pinned to `python:3.11-slim` (docker/Dockerfile), where `uuid` exposes only
`uuid1`, `uuid3`, `uuid4` and `uuid5`, and `requirements.txt` carries no UUID
package. So the layout is assembled here, per the RFC, and unit-tested in
`tests/utils/test_uuid7.py`.

Why not the PostgreSQL 18 `uuidv7()`
------------------------------------
The generator is deliberately APP-SIDE, in one place, and no column carries a
server default:

  * The migration has to run on the owner's dev cluster, which is PostgreSQL
    16.9. `ALTER TABLE … SET DEFAULT uuidv7()` is validated when the DDL is
    parsed, so it fails outright on 16 — a version-conditional default would
    leave 16 and 18 installs with genuinely different schemas.
  * The test suite runs on SQLite in memory, which has no `uuidv7()` at all. A
    PostgreSQL-only default would mean the suite that proves authorisation
    exercises a different schema from production.
  * A server default silently papers over an application path that forgot to
    set the identifier. Without one, that path raises a NOT NULL violation on
    the first insert — in the test suite, not in production.

Rows inserted outside the application therefore have to supply the value
themselves; that is the same contract every other invariant in this schema
already has.

Layout (RFC 9562 §5.7), most significant bit first
--------------------------------------------------
    48 bits  unix_ts_ms   big-endian Unix epoch milliseconds
     4 bits  ver          0b0111
    12 bits  rand_a       intra-millisecond counter (see below)
     2 bits  var          0b10
    62 bits  rand_b       `secrets` randomness
   ---------
   128 bits

The value is assembled as a single 128-bit integer and handed to
`uuid.UUID(int=…)`, which serialises most-significant-byte first — i.e. the
timestamp occupies the leading six bytes in big-endian order, as required.

Time source
-----------
`time.time_ns()` — UTC epoch nanoseconds. Deliberately NOT `now_local()` from
`app.utils.datetime_utils`: that returns tz-aware America/Ciudad_Juarez, and a
local wall clock jumps backwards an hour at the DST boundary, which would break
the global time ordering that is the entire reason for choosing v7 over v4.

Monotonicity
------------
Ordering across milliseconds comes free from the timestamp. Within a single
millisecond, `rand_a` is used as the RFC 9562 §6.2 fixed-length dedicated
counter: it is seeded with 11 random bits when the millisecond changes (leaving
at least 2048 increments of headroom in the 12-bit field) and incremented for
every subsequent value in that same millisecond. If the counter would overflow,
the timestamp is advanced by one millisecond and the counter re-seeded, so the
sequence stays strictly increasing. The same branch absorbs a clock that steps
backwards: emitted timestamps never decrease.
"""
from __future__ import annotations

import secrets
import threading
import time
import uuid

__all__ = ['uuid7', 'uuid7_str', 'parse_uuid']


_VERSION = 0b0111          # 4 bits
_VARIANT = 0b10            # 2 bits, RFC 9562 variant

_COUNTER_BITS = 12         # rand_a
_COUNTER_MAX = (1 << _COUNTER_BITS) - 1
#: Seed the counter with fewer bits than it holds so there is guaranteed
#: headroom to increment inside one millisecond before it can overflow.
_COUNTER_SEED_BITS = 11

_RAND_B_BITS = 62

_lock = threading.Lock()
_last_ms = -1
_last_counter = 0


def _next_timestamp_and_counter() -> tuple[int, int]:
    """
    Return the (unix_ts_ms, rand_a) pair for the next identifier.

    Guarantees the pair is strictly greater than the previously returned one,
    which makes the resulting UUIDs strictly increasing under the lexicographic
    / binary comparison used by the database.
    """
    global _last_ms, _last_counter

    with _lock:
        now_ms = time.time_ns() // 1_000_000

        if now_ms > _last_ms:
            _last_ms = now_ms
            _last_counter = secrets.randbits(_COUNTER_SEED_BITS)
        elif _last_counter < _COUNTER_MAX:
            # Same millisecond (or the clock stepped backwards): hold the
            # timestamp and advance the counter.
            _last_counter += 1
        else:
            # Counter exhausted inside one millisecond — borrow from the
            # future rather than emit a duplicate or a smaller value.
            _last_ms += 1
            _last_counter = secrets.randbits(_COUNTER_SEED_BITS)

        return _last_ms, _last_counter


def uuid7() -> uuid.UUID:
    """Generate a time-ordered UUIDv7 (RFC 9562 §5.7)."""
    ts_ms, counter = _next_timestamp_and_counter()
    rand_b = secrets.randbits(_RAND_B_BITS)

    value = (ts_ms & 0xFFFF_FFFF_FFFF) << 80   # 48 bits, most significant
    value |= _VERSION << 76                    # 4 bits
    value |= (counter & _COUNTER_MAX) << 64    # 12 bits (rand_a)
    value |= _VARIANT << 62                    # 2 bits
    value |= rand_b                            # 62 bits

    return uuid.UUID(int=value)


def uuid7_str() -> str:
    """Canonical hyphenated string form of a fresh UUIDv7."""
    return str(uuid7())


def parse_uuid(value) -> uuid.UUID | None:
    """
    Coerce `value` to a `uuid.UUID`, returning None when it is not one.

    Never raises. This is what lets a route turn an unparseable path segment
    into a clean 404 instead of a 500 — and, just as importantly, makes a
    malformed identifier indistinguishable from a well-formed one that names a
    row the caller may not see.

    Accepts an existing `uuid.UUID`, or any string form `uuid.UUID` itself
    accepts (hyphenated, bare hex, braced, `urn:uuid:` prefixed). Every one of
    those spellings denotes the same identifier, so none of them can resolve to
    a different row.
    """
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
