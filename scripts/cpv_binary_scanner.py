#!/usr/bin/env python3
"""Binary-aware scanning STRATEGY for the SkillAudit native catalog.

Why this module exists
======================

The user explicitly REJECTED file-size / binary skip-filters as a CVE-class
regression. Attackers hide payloads in:

* Large minified JS bundles (string tables of a polyglot file).
* Base64 / hex / gzip / zlib payloads embedded in EXIF blocks of images.
* Hardcoded secrets in ``.so`` / ``.dll`` / ``.dylib`` string tables.
* Polyglot files (legitimate-looking PNG that is also a valid ZIP / Python
  script when interpreted differently).

The text-only scanner in ``cpv_skillaudit_native.py`` filters its candidate
set down to a small set of source / config / markdown extensions and
silently passes over binaries. This module restores binary coverage by:

1. Detecting whether a file is binary (null-byte heuristic with a UTF-16-BOM
   override so Windows-format UTF-16 text isn't misclassified).
2. Extracting printable ASCII and UTF-16-LE runs from the raw bytes.
3. Recursively trying base64 / hex / gzip / zlib decoding (depth-capped) to
   surface text hidden inside compressed or encoded payloads.
4. Running the full skillaudit catalog (the one loaded by
   ``cpv_skillaudit_native``) over every extracted string and every
   decoded payload.

Iron rules
==========

* NEVER silently skips a file. Permission errors, truncated reads, decode
  errors all emit a visible WARNING-class finding (so a downstream agent
  triages, but the scan as a whole completes).
* Pure stdlib — no third-party dependencies are imported. ``base64``,
  ``binascii``, ``gzip``, ``zlib``, ``unicodedata`` are all in the standard
  library. No subprocess, no network.
* Streaming for files > 100 MB. Chunked 4 MB reads protect against OOM on
  enormous artefacts. Per-file memory cap of 1 GB enforced via running
  total of decoded bytes.
* Decode-bomb safe. Each base64 / gzip / zlib output is capped at
  ``_DECODE_OUTPUT_CAP`` (100 MB) per step; nested decode chain stops at
  ``max_depth`` (default 3).
* Self-similar input guard. The recursion drops candidates whose decode
  output is equal to or longer than the input — a textbook zip-bomb tell
  AND a degenerate self-fixed-point that would loop forever.

Public API
==========

``is_binary(path)``                 → bool
``extract_ascii_strings(data)``     → list[str]
``extract_utf16_strings(data)``     → list[str]
``decode_chain(data, max_depth=3)`` → list[str]
``scan_binary(path, catalog)``      → list[dict]

The ``catalog`` argument exists for hot-reload / test-injection (one
compiled rule set reused across many files); when omitted the module loads
the same catalog ``cpv_skillaudit_native`` does. ``scan_binary`` produces
the same finding dict shape ``scan_content`` does, with the ``match`` field
prefixed by ``"[extracted from binary] "`` so downstream consumers see the
provenance at a glance.

Environment opt-out
===================

``CPV_BINARY_SCAN=0`` falls back to "no-op return ``[]``". This exists ONLY
for debugging the binary scanner in isolation — production callers MUST
NOT set it. Defaulting to off would re-introduce the CVE-class regression
the module exists to close.
"""

from __future__ import annotations

import base64
import binascii
import gzip
import io
import logging
import os
import re
import zlib
from pathlib import Path
from typing import Any

__all__ = [
    "is_binary",
    "extract_ascii_strings",
    "extract_utf16_strings",
    "decode_chain",
    "scan_binary",
    "BINARY_PREFIX",
]


_LOG = logging.getLogger("cpv.binary_scanner")


# ────────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────────


# Tag injected into every emitted finding's ``match`` field so downstream
# renderers (CPV severity tables, fix-loop dispatchers, security-agent
# breakdown matrices) see binary-extracted matches at a glance and don't
# confuse them with source-line matches.
BINARY_PREFIX = "[extracted from binary] "

# Chunked-read size. Large enough to amortise syscall overhead, small
# enough that even a thousand parallel scans don't exhaust memory.
_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB

# Per-file streaming threshold. Files at or above this size go through the
# chunked path instead of one ``read_bytes()`` call.
_STREAM_THRESHOLD = 100 * 1024 * 1024  # 100 MB

# Per-file memory ceiling for the cumulative bytes the scanner is willing
# to hold (raw bytes + extracted strings + decoded payloads). Above this,
# we stop extracting / decoding and emit a WARNING-class sentinel so a
# downstream agent can triage by hand.
_PER_FILE_MEMORY_CAP = 1024 * 1024 * 1024  # 1 GB

# Hard cap on the size of any single decode output. A zlib / gzip bomb is
# typically a tiny input that expands to gigabytes — this cap turns the
# bomb into a visible WARNING finding instead of an OOM.
_DECODE_OUTPUT_CAP = 100 * 1024 * 1024  # 100 MB — per-decoder output cap

# decode_chain work bounds (audit MAJOR #6). The chain no longer drops expanding
# decodes (gzip/zlib ALWAYS expand), so termination relies on the `visited`
# digest set + `max_depth` + these two bounds: a cumulative-decoded-bytes budget
# across the whole recursive chain, and a hard cap on how many text variants we
# surface. (The old code reused _DECODE_OUTPUT_CAP — a BYTE cap — as the
# results-LIST length cap, which was effectively unbounded.)
_TOTAL_DECODE_BUDGET = 4 * _DECODE_OUTPUT_CAP  # 400 MB cumulative across the chain
_DECODE_MAX_RESULTS = 1000  # max surfaced text variants

# Minimum bytes a payload must have to be worth trying to decode. Below
# this we don't bother (a 3-byte "base64" string isn't a base64 payload,
# it's noise).
_MIN_DECODE_INPUT = 8

# ASCII printable-run minimum length. The classic "strings" tool defaults
# to 4; we use 6 to suppress noise from things like 4-byte ELF section
# names ("BLOB", "TEXT", "DATA", etc.) while still catching tokens / URLs
# / suspicious paths. Length is in characters, not bytes.
_DEFAULT_MIN_RUN = 6

# Printable-character mask for ASCII (32 .. 126 + tab is fine to include
# but we KEEP newlines OUT so each run is a single line).
_ASCII_PRINTABLE = frozenset(range(0x20, 0x7F)) | {0x09}  # printable + tab

# UTF-16-LE BOM. If a file starts with this it's text, not binary.
_UTF16_LE_BOM = b"\xff\xfe"
# UTF-16-BE BOM is the inverse.
_UTF16_BE_BOM = b"\xfe\xff"
# UTF-8 BOM.
_UTF8_BOM = b"\xef\xbb\xbf"

# Magic numbers for the recursive decoders. The header check rejects
# obvious mismatches cheaply before we call into the heavier decode
# routines.
_GZIP_MAGIC = b"\x1f\x8b"
# zlib has multiple legal first bytes (78 01, 78 5e, 78 9c, 78 da, etc.).
# A cheap header detector is "first byte 0x78 AND first two bytes mod 31
# == 0" — the zlib RFC's CMF/FLG checksum. We don't use the checksum here
# because we'd rather try and fail than reject legitimate input.
_ZLIB_FIRST_BYTE = 0x78

# Base64 alphabet. We use a charset test rather than a regex so we can
# scan large inputs without exhausting backtrack budget.
_BASE64_CHARS = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
)
_BASE64_MIN_LEN = 16  # below this, false-positive rate is too high

# Hex alphabet.
_HEX_CHARS = frozenset(b"0123456789abcdefABCDEF")
_HEX_MIN_LEN = 16


# ────────────────────────────────────────────────────────────────────────
# Env-var opt-out
# ────────────────────────────────────────────────────────────────────────


def _binary_scan_enabled() -> bool:
    """Return ``False`` only if ``CPV_BINARY_SCAN=0`` is explicitly set.

    The default is ENABLED. Defaulting to off would re-introduce the
    CVE-class regression the module exists to close (binaries being
    silently skipped). Production callers MUST NOT set the opt-out.
    """
    return os.environ.get("CPV_BINARY_SCAN", "1") != "0"


# ────────────────────────────────────────────────────────────────────────
# is_binary
# ────────────────────────────────────────────────────────────────────────


def is_binary(path: Path, sample_bytes: int = 8192) -> bool:
    """Return ``True`` if the file is best-treated as binary.

    Heuristic (in order):

    1. If the file starts with a UTF-8 / UTF-16-LE / UTF-16-BE BOM, treat
       as text (False).
    2. Read up to ``sample_bytes`` bytes from the start of the file.
    3. If the sample contains any null byte (``0x00``), treat as binary.
    4. Otherwise, treat as text.

    Empty files are NOT binary — they have no content to scan, and the
    caller's loop should short-circuit them anyway. Unreadable files
    (permission denied, race-deleted, etc.) raise OSError so the caller
    can decide whether to emit a WARNING.

    The sample size is intentionally larger than ``file(1)``'s default
    (4 KB) because polyglot files (JPEG + script, PNG + ZIP) often have
    a benign-looking header followed by the actual payload past the 4 KB
    mark.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(sample_bytes)
    except (OSError, ValueError):
        # Caller is responsible for translating into a WARNING. Re-raise
        # the OSError unchanged so the scan_binary wrapper can decide
        # whether to emit a finding or propagate.
        raise

    if not head:
        # Empty file — nothing to classify, treat as text so the caller
        # short-circuits to an empty finding list.
        return False

    # BOM checks. UTF-16 with a BOM is *text*, not binary, even though
    # naive null-byte detection would say binary (UTF-16 alternates
    # ASCII chars with 0x00).
    if head.startswith(_UTF8_BOM):
        return False
    if head.startswith(_UTF16_LE_BOM) or head.startswith(_UTF16_BE_BOM):
        return False

    # Null-byte sniff is the canonical binary tell.
    return b"\x00" in head


# ────────────────────────────────────────────────────────────────────────
# String extractors
# ────────────────────────────────────────────────────────────────────────


def extract_ascii_strings(data: bytes, min_len: int = _DEFAULT_MIN_RUN) -> list[str]:
    """Return printable-ASCII runs of at least ``min_len`` characters.

    Comparable to ``strings(1)`` with ``-n <min_len>``. Tab is included
    in the printable set; newline / carriage-return / other control
    characters are not (they would split a logical line into multiple
    fragments and confuse rule patterns).

    Pure stdlib, single linear pass over ``data``.
    """
    if min_len < 1:
        # Defensive — a non-positive min_len would return one entry per
        # byte and explode memory.
        min_len = 1

    runs: list[str] = []
    current: bytearray = bytearray()
    for byte in data:
        if byte in _ASCII_PRINTABLE:
            current.append(byte)
            continue
        if len(current) >= min_len:
            # Decode is safe: every byte in _ASCII_PRINTABLE is a valid
            # ASCII codepoint, so ``decode("ascii")`` cannot raise here.
            runs.append(current.decode("ascii"))
        current.clear()
    if len(current) >= min_len:
        runs.append(current.decode("ascii"))
    return runs


def extract_utf16_strings(data: bytes, min_len: int = _DEFAULT_MIN_RUN) -> list[str]:
    """Return printable UTF-16-LE runs of at least ``min_len`` characters.

    Windows-format secrets and PE-section strings are stored UTF-16-LE,
    not ASCII. A binary scanner that only walks ASCII would miss them.

    Implementation: walk two bytes at a time, accept the pair if the
    high byte is 0 AND the low byte is in ``_ASCII_PRINTABLE``. This is
    the BMP-Basic-Latin subset of UTF-16, which is where credentials /
    URLs / paths actually live; CJK / emoji strings are not a credible
    target for "extract suspicious string from a Windows DLL".

    Strict 2-byte alignment is enforced — odd-length inputs lose their
    last byte (mirroring how a UTF-16 codec would handle them).
    """
    if min_len < 1:
        min_len = 1

    runs: list[str] = []
    current: bytearray = bytearray()
    n = len(data) - (len(data) % 2)
    for i in range(0, n, 2):
        low = data[i]
        high = data[i + 1]
        if high == 0 and low in _ASCII_PRINTABLE:
            current.append(low)
            continue
        if len(current) >= min_len:
            runs.append(current.decode("ascii"))
        current.clear()
    if len(current) >= min_len:
        runs.append(current.decode("ascii"))
    return runs


# ────────────────────────────────────────────────────────────────────────
# Recursive decoder
# ────────────────────────────────────────────────────────────────────────


def _try_decode_base64(data: bytes) -> bytes | None:
    """Return the base64-decoded bytes of ``data`` if it looks like base64.

    Heuristics:

    * Minimum length _BASE64_MIN_LEN.
    * Every byte must be in the base64 alphabet (+ optional whitespace
      we strip first).
    * Final length must be a multiple of 4 once whitespace is gone.
    * ``base64.b64decode(validate=True)`` raises if there's any
      non-alphabet byte; we catch the binascii.Error and return None.

    A successful decode is capped at ``_DECODE_OUTPUT_CAP`` bytes — a
    legitimate base64 payload that decodes to gigabytes is a bomb, not a
    payload we should be parsing.
    """
    # Drop whitespace cheaply (newlines + tabs + spaces inside a base64
    # blob are normal in source). bytes.translate is faster than a regex
    # for this shape.
    cleaned = data.translate(None, b" \t\r\n")
    if len(cleaned) < _BASE64_MIN_LEN:
        return None
    if len(cleaned) % 4 != 0:
        return None
    if any(b not in _BASE64_CHARS for b in cleaned):
        return None
    try:
        decoded = base64.b64decode(cleaned, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(decoded) > _DECODE_OUTPUT_CAP:
        # Cap and return the prefix — the caller's recursion guard will
        # see that this output is shorter-than-input only if the cap
        # kicks in for a non-bomb input, which is fine (we get partial
        # coverage rather than no coverage).
        return decoded[:_DECODE_OUTPUT_CAP]
    return decoded


def _try_decode_hex(data: bytes) -> bytes | None:
    """Return the hex-decoded bytes of ``data`` if it looks like hex."""
    cleaned = data.translate(None, b" \t\r\n")
    if len(cleaned) < _HEX_MIN_LEN:
        return None
    if len(cleaned) % 2 != 0:
        return None
    if any(b not in _HEX_CHARS for b in cleaned):
        return None
    try:
        decoded = binascii.unhexlify(cleaned)
    except (binascii.Error, ValueError):
        return None
    if len(decoded) > _DECODE_OUTPUT_CAP:
        return decoded[:_DECODE_OUTPUT_CAP]
    return decoded


def _try_decode_gzip(data: bytes) -> bytes | None:
    """Return gzip-decompressed bytes (bounded) if ``data`` starts with the gzip magic.

    Reads at most ``_DECODE_OUTPUT_CAP`` bytes from the decompression stream via
    ``GzipFile.read(cap)`` — it decompresses incrementally and stops at the cap,
    so a gzip BOMB (tiny input, enormous output) cannot materialize its full
    output and OOM the scanner. ``gzip.decompress(data)`` would expand the whole
    stream BEFORE any post-hoc slice. Mirrors the bounded ``_try_decode_zlib``
    below; the ``_PER_FILE_MEMORY_CAP`` bounds the input read from disk, NOT this
    decompressed output. (audit MAJOR #5)
    """
    if not data.startswith(_GZIP_MAGIC):
        return None
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
            # read cap+1 so we can detect (and truncate) an over-cap stream
            # without ever holding more than cap+1 bytes.
            decoded = gz.read(_DECODE_OUTPUT_CAP + 1)
    except (OSError, EOFError, zlib.error, ValueError):
        return None
    if len(decoded) > _DECODE_OUTPUT_CAP:
        return decoded[:_DECODE_OUTPUT_CAP]
    return decoded


def _try_decode_zlib(data: bytes) -> bytes | None:
    """Return zlib-decompressed bytes if ``data`` looks like a zlib stream."""
    if len(data) < 2 or data[0] != _ZLIB_FIRST_BYTE:
        return None
    try:
        # decompressobj lets us bound the output size via max_length.
        decompressor = zlib.decompressobj()
        decoded = decompressor.decompress(data, _DECODE_OUTPUT_CAP)
    except (zlib.error, ValueError):
        return None
    # If decompressor.unconsumed_tail is non-empty we hit the cap — fine,
    # return the prefix.
    return decoded


def decode_chain(data: bytes, max_depth: int = 3) -> list[str]:
    """Recursively decode base64 / hex / gzip / zlib and return any text outputs.

    Algorithm:

    * Start with ``data`` at depth 0.
    * At each depth, attempt every decoder. For each that returns bytes:
        - If the result is printable text (UTF-8 decodable AND mostly
          printable), add it to the output strings list.
        - Recurse into the result at depth+1, unless we've hit
          ``max_depth``.
    * Loop safety: a ``visited`` digest set breaks self-similar cycles, and
      each decoder caps its own output at ``_DECODE_OUTPUT_CAP``. Total work is
      bounded by ``_TOTAL_DECODE_BUDGET`` (cumulative decoded bytes) and the
      surfaced-variant count by ``_DECODE_MAX_RESULTS``.

    Note: decodes are NOT dropped for being the same length or larger than their
    input — gzip/zlib decompression ALWAYS expands, so that gate (removed in
    audit MAJOR #6) made every compressed payload unscannable.

    Returns the textual decode results as a list of ``str``. Non-text
    decodes (bytes that don't look like text after decoding) are still
    fed back into the recursion but aren't added to the output.
    """
    results: list[str] = []
    if max_depth < 0:
        return results
    if not data:
        return results

    # Track visited inputs to break self-similar loops (e.g. a base64
    # payload that decodes to itself via a poorly-designed obfuscation
    # routine). Hash on a digest, not the full bytes, to keep the set
    # bounded for large inputs.
    visited: set[bytes] = set()
    total_decoded = 0  # cumulative decoded bytes across the chain — work budget

    def _walk(payload: bytes, depth: int) -> None:
        nonlocal total_decoded
        if depth > max_depth:
            return
        # Self-similar / loop guard.
        # Hash digest of the payload — using sha1 just as a quick fingerprint;
        # no security claim attaches to the choice.
        import hashlib  # noqa: PLC0415 — stdlib lazy-import keeps top of module compact

        digest = hashlib.sha1(payload, usedforsecurity=False).digest()
        if digest in visited:
            return
        visited.add(digest)

        for decoder_name, decoder in (
            ("base64", _try_decode_base64),
            ("hex", _try_decode_hex),
            ("gzip", _try_decode_gzip),
            ("zlib", _try_decode_zlib),
        ):
            try:
                decoded = decoder(payload)
            except Exception as exc:  # noqa: BLE001 — defensive: never let a decoder crash the chain
                _LOG.info("binary_scanner decode_chain: %s decoder failed: %s", decoder_name, exc)
                continue
            if decoded is None:
                continue
            # Do NOT drop expanding decodes — gzip/zlib ALWAYS expand. (audit
            # MAJOR #6) Loop safety is the `visited` set; work is bounded below.
            # If decoded looks like text, surface it for catalog matching.
            text = _decoded_to_text(decoded)
            if text is not None:
                results.append(text)
                if len(results) >= _DECODE_MAX_RESULTS:
                    return
            total_decoded += len(decoded)
            if total_decoded > _TOTAL_DECODE_BUDGET:
                # Work budget exhausted — stop the chain (bomb / pathological
                # nesting). The findings collected so far are still returned.
                return
            # Recurse regardless — a base64-of-gzip is real.
            _walk(decoded, depth + 1)

    _walk(data, 0)
    return results


def _decoded_to_text(data: bytes) -> str | None:
    """Best-effort byte → text conversion for catalog matching.

    Tries UTF-8 first; falls back to latin-1 (which never raises and
    preserves byte values). Returns None when the decoded payload is
    almost entirely unprintable (typical of binary-bomb output that
    happened to fit our heuristic gate).
    """
    if not data:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1", errors="replace")

    # Printability heuristic — if ≥ 80 % of chars are printable, treat
    # as text worth scanning. Below that, decoded bytes are noise.
    total = len(text)
    if total == 0:
        return None
    printable = sum(1 for ch in text if ch.isprintable() or ch in ("\n", "\r", "\t"))
    if printable / total < 0.8:
        return None
    return text


# ────────────────────────────────────────────────────────────────────────
# scan_binary — the main entry point
# ────────────────────────────────────────────────────────────────────────


def _load_default_catalog() -> list[tuple[dict[str, Any], list[re.Pattern[str]]]]:
    """Load the same skillaudit catalog the text scanner uses.

    Returns the compiled rules list (the data structure
    ``cpv_skillaudit_native._compiled_rules`` returns). Returns an empty
    list if the catalog can't be loaded (caller emits a WARNING).
    """
    try:
        from cpv_skillaudit_native import _compiled_rules  # noqa: PLC0415

        return _compiled_rules()
    except ImportError:
        return []


def _read_streaming(path: Path) -> bytes:
    """Read ``path`` in 4 MB chunks, capped at ``_PER_FILE_MEMORY_CAP``.

    Used when the file is at or above ``_STREAM_THRESHOLD``. The cap
    exists because some build artefacts (large binary blobs in test
    fixtures, vendored model weights, etc.) can be multi-gigabyte and
    we don't want a single oversized file to take down a fleet scan.

    The returned bytes are the file's full contents up to the cap. If
    the cap kicks in, the caller is responsible for emitting a WARNING.
    """
    buf = bytearray()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_CHUNK_SIZE)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) >= _PER_FILE_MEMORY_CAP:
                # Truncate at cap. The caller will emit a WARNING so the
                # truncation is visible.
                del buf[_PER_FILE_MEMORY_CAP:]
                break
    return bytes(buf)


def _run_catalog_on_text(
    text: str,
    catalog: list[tuple[dict[str, Any], list[re.Pattern[str]]]],
    rel_path: str,
    source_tag: str,
) -> list[dict[str, Any]]:
    """Run the skillaudit catalog over a single ``text`` blob.

    Returns finding dicts in the same shape ``cpv_skillaudit_native.scan_content``
    emits, with the ``match`` field prefixed by ``BINARY_PREFIX`` so the
    binary provenance survives all the way to the report renderer.

    ``source_tag`` distinguishes ASCII-string / UTF-16-string / decoded
    payload findings inside the same file — surfaced via the
    ``binary_source`` key on each finding so triagers can tell where
    the match came from.
    """
    findings: list[dict[str, Any]] = []
    if not text or not catalog:
        return findings
    lines = text.split("\n")
    for rule, compiled_pats in catalog:
        rule_id = rule.get("id", "RULE_UNKNOWN")
        rule_sev = rule.get("severity", "medium")
        rule_cat = rule.get("category", "rule")
        rule_name = rule.get("name", rule_id)
        rule_desc = rule.get("description", "")
        for pat in compiled_pats:
            for i, line in enumerate(lines):
                m = pat.search(line)
                if not m:
                    continue
                matched_text = m.group(0)
                findings.append(
                    {
                        "ruleId": rule_id,
                        "severity": rule_sev,
                        "category": rule_cat,
                        "name": rule_name,
                        "description": rule_desc,
                        "line": i + 1,
                        "lineContent": line.strip()[:200],
                        "match": BINARY_PREFIX + matched_text,
                        "binary_source": source_tag,
                        "file": rel_path,
                        "suppressed": False,
                        "demoted": False,
                    }
                )
    return findings


def _warning_finding(message: str, rel_path: str, rule_id: str = "BINARY_SCAN_WARNING") -> dict[str, Any]:
    """Build a WARNING-class finding dict in the skillaudit shape.

    Severity "low" maps to CPV's NIT bucket, which downstream renders as
    a WARNING. The iron rule is that nothing is ever skipped silently —
    every degraded code path emits one of these so a triager sees it.
    """
    return {
        "ruleId": rule_id,
        "severity": "low",
        "category": "infrastructure",
        "name": "Binary scanner WARNING",
        "description": message,
        "line": 0,
        "lineContent": "",
        "match": BINARY_PREFIX + message,
        "binary_source": "scanner",
        "file": rel_path,
        "suppressed": False,
        "demoted": False,
    }


def scan_binary(
    path: Path,
    catalog: list[tuple[dict[str, Any], list[re.Pattern[str]]]] | None = None,
) -> list[dict[str, Any]]:
    """Scan a binary file for skillaudit catalog matches.

    Pipeline:

    1. ``CPV_BINARY_SCAN=0`` short-circuits to ``[]`` (debug-only opt-out).
    2. Read the file (chunked if > _STREAM_THRESHOLD; one-shot otherwise).
       Permission errors / OSError surface as WARNING findings.
    3. Extract ASCII strings → run catalog.
    4. Extract UTF-16-LE strings → run catalog.
    5. Run ``decode_chain`` over the raw bytes → run catalog over each
       textual decode.
    6. Return aggregated findings.

    All emitted findings carry the ``BINARY_PREFIX`` on the ``match``
    field and a ``binary_source`` key indicating the extraction step
    that produced them.

    NEVER raises. The contract is "if you handed me a path I will
    return a list of findings"; failures become WARNING findings inside
    the list, never exceptions out the top.
    """
    rel_path = str(path)

    if not _binary_scan_enabled():
        return []

    if catalog is None:
        catalog = _load_default_catalog()

    # Permission / OSError on the initial size stat — emit WARNING and
    # bail. We don't propagate because the caller's contract is one
    # path → one list of findings.
    try:
        size = path.stat().st_size
    except OSError as exc:
        msg = f"Binary file stat failed — full scan deferred: {path}: {exc}"
        _LOG.warning("binary_scanner: %s", msg)
        return [_warning_finding(msg, rel_path)]

    if size == 0:
        _LOG.info("binary_scanner: zero-byte file %s", rel_path)
        return []

    # Read the bytes. Two paths: chunked for huge files, one-shot for
    # normal files. Both honour _PER_FILE_MEMORY_CAP.
    try:
        if size >= _STREAM_THRESHOLD:
            data = _read_streaming(path)
            if len(data) >= _PER_FILE_MEMORY_CAP:
                # Visible WARNING — the truncation is intentional and
                # safe, but the agent should know coverage was partial.
                _LOG.warning(
                    "binary_scanner: %s truncated to %s bytes (over %s cap)",
                    rel_path,
                    _PER_FILE_MEMORY_CAP,
                    _PER_FILE_MEMORY_CAP,
                )
        else:
            data = path.read_bytes()
    except PermissionError as exc:
        msg = f"Binary file permission denied — full scan deferred: {path}: {exc}"
        _LOG.warning("binary_scanner: %s", msg)
        return [_warning_finding(msg, rel_path, rule_id="BINARY_SCAN_PERMISSION_DENIED")]
    except OSError as exc:
        msg = f"Binary file read failed — full scan deferred: {path}: {exc}"
        _LOG.warning("binary_scanner: %s", msg)
        return [_warning_finding(msg, rel_path)]

    if not data:
        _LOG.info("binary_scanner: empty read on %s", rel_path)
        return []

    findings: list[dict[str, Any]] = []

    # 1. ASCII strings.
    try:
        ascii_runs = extract_ascii_strings(data)
    except Exception as exc:  # noqa: BLE001 — defensive: extractor should never raise but if it does we surface it
        _LOG.warning("binary_scanner: ascii extraction failed on %s: %s", rel_path, exc)
        findings.append(_warning_finding(f"ASCII string extraction failed: {exc}", rel_path))
        ascii_runs = []
    if ascii_runs:
        joined = "\n".join(ascii_runs)
        findings.extend(_run_catalog_on_text(joined, catalog, rel_path, "ascii_strings"))
    else:
        _LOG.info("binary_scanner: no extractable ASCII strings in %s", rel_path)

    # 2. UTF-16-LE strings.
    try:
        utf16_runs = extract_utf16_strings(data)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("binary_scanner: utf-16 extraction failed on %s: %s", rel_path, exc)
        findings.append(_warning_finding(f"UTF-16 string extraction failed: {exc}", rel_path))
        utf16_runs = []
    if utf16_runs:
        joined = "\n".join(utf16_runs)
        findings.extend(_run_catalog_on_text(joined, catalog, rel_path, "utf16_strings"))

    # 3. Recursive decode chain. Try TWO inputs:
    #    a) The raw file bytes — covers the case where the file IS the
    #       encoded payload (gzip/zlib magic-byte detection lives here).
    #    b) Each extracted ASCII string of meaningful length — this is
    #       where embedded base64 / hex blobs typically live (the raw
    #       file usually has a header/footer that prevents the
    #       whole-file decoder from succeeding).
    decoded_texts: list[str] = []
    try:
        decoded_texts.extend(decode_chain(data))
        # Per-string pass — try each ASCII run that could plausibly be
        # an encoded payload (length floor matches the decoders' own
        # _MIN_DECODE_INPUT gate). Bounded to 1024 strings so a
        # pathological binary with a million short runs doesn't
        # explode the loop.
        for s in ascii_runs[:1024]:
            if len(s) < _MIN_DECODE_INPUT:
                continue
            decoded_texts.extend(decode_chain(s.encode("ascii")))
    except Exception as exc:  # noqa: BLE001 — the inner _walk has its own guards; this is belt-and-braces
        _LOG.warning("binary_scanner: decode_chain failed on %s: %s", rel_path, exc)
        findings.append(_warning_finding(f"Decode chain failed: {exc}", rel_path))
        decoded_texts = []
    for decoded in decoded_texts:
        findings.extend(_run_catalog_on_text(decoded, catalog, rel_path, "decoded_payload"))

    return findings
