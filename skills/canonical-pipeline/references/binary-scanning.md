# Binary-aware scanning (v2.104.0+)

## Table of contents

- [The security principle: NEVER skip a file](#the-security-principle-never-skip-a-file)
- [Detection](#detection)
- [Scanning pipeline](#scanning-pipeline)
- [Edge cases (ALL logged, NEVER silent)](#edge-cases-all-logged-never-silent)
- [Env-var](#env-var)
- [What this scanner does NOT do](#what-this-scanner-does-not-do)
- [Coverage gain vs current text-only behavior](#coverage-gain-vs-current-text-only-behavior)
- [Performance characteristics](#performance-characteristics)
- [Future work](#future-work)

Every CPV security scanner runs against EVERY file in the plugin — text
and binary alike. v2.104.0 replaces the dangerous "skip binaries"
anti-pattern with a binary-aware extraction strategy: ASCII / UTF-16
printable runs + recursive decode chain (base64 / hex / gzip / zlib), then
the full skillaudit rule catalog runs against the extracted text. Findings
keep their full rule semantics; the only difference is a
`[extracted from binary]` prefix that tells the reviewer the original byte
window was non-textual.

## The security principle: NEVER skip a file

**"File-size and binary pre-filters surrender exactly what the scanner
exists to catch."** Any code path that says "this file is too big" or
"this file looks binary, skip it" is a covert exemption — an attacker who
knows the filter exists will engineer their payload to land inside the
exempt region. The security guarantee a scanner makes is only as strong
as its weakest skip rule.

Attack vectors that justify scanning binaries end-to-end:

| Vector | Why text-only scans miss it |
|---|---|
| 50 MB minified `vendor.bundle.js` in `node_modules/` | Supply-chain malware hides in minified webpack bundles; size cutoffs are the #1 attacker bypass |
| Base64 payloads in PNG / JPG EXIF or LSB | Steganographic exfiltration — the image is a valid PNG so a "skip binaries" filter swallows it whole |
| Hardcoded secrets in compiled `.so` / `.dll` / `.exe` string tables | Linker preserves string constants verbatim; `strings`-style extraction finds them but only if you actually scan the binary |
| Polyglot files (valid PNG + valid Python) | File magic says "image"; the embedded Python is invisible to filters that key on magic bytes |
| `.pyc` bytecode with embedded source constants | CPython `co_consts` retains all string literals; an attacker can ship `.pyc` without `.py` and a text-only scanner sees nothing |
| Minified `.min.js` over a hypothetical size cutoff | Any per-file size limit is the attacker's free-pass region |
| Bundled WASM with imports / exports / URLs | The WASM string section is plain ASCII; suspicious URLs and exfil endpoints sit there waiting for `strings`-style extraction |

Every one of these is found by running the full skillaudit catalog on
extracted strings. No file is skipped. No size cap is imposed. If the
scanner cannot read a file, that fact is itself a WARNING finding —
never a silent skip.

## Detection

Binary classification runs on the first 8 KB of every file:

1. **Null-byte sniff** — any `\x00` in the first 8 KB → treat as binary.
2. **UTF-16 BOM check** — leading `\xff\xfe` (UTF-16-LE) or `\xfe\xff`
   (UTF-16-BE) → treat as UTF-16 text (decode then scan normally).

Files that pass both checks are treated as UTF-8 / ASCII text and scanned
by the existing skillaudit text path unchanged. Files that fail null-byte
sniff enter the binary scanning pipeline below. Files smaller than 8 KB
use their full length as the detection window.

The 8 KB window is the same threshold `git` uses for its own binary
detection (`buffer_is_binary` in `xdiff-interface.c`) — chosen empirically
to be small enough that the cost is negligible and large enough that the
"text with a stray null byte" false positive rate is near zero on real
codebases.

## Scanning pipeline

For every file that detection classifies as binary:

### Step 1 — ASCII printable-run extraction (≥ 6 chars)

Walk the byte stream. Emit every contiguous run of `0x20`-`0x7E` (printable
ASCII) of length ≥ 6 as a candidate string. The 6-character floor matches
GNU `strings -n 6` and discards alphabet-noise like one-byte UTF-8
sequences that happen to land on printable codepoints.

### Step 2 — UTF-16-LE printable-run extraction

Same as Step 1 but stride-2: emit every contiguous run of `(printable,
0x00, printable, 0x00, …)` pairs of pair-length ≥ 6. This catches Windows
DLL / EXE string tables and any UTF-16 payload embedded in a binary
container.

### Step 3 — Decode chain (base64, hex, gzip, zlib — recursive, max depth 3)

Every extracted string is fed through a decoder loop:

1. If the string matches the base64 alphabet `[A-Za-z0-9+/=]{16,}` and
   decodes cleanly → emit the decoded bytes.
2. If the string matches the hex alphabet `[0-9A-Fa-f]{16,}` with even
   length and decodes cleanly → emit the decoded bytes.
3. If the bytes start with `\x1f\x8b` → gunzip → emit the inflated bytes.
4. If the bytes start with `\x78\x9c` / `\x78\xda` / `\x78\x01` (zlib
   header) → zlib-decompress → emit the inflated bytes.

Each decoded layer re-enters Step 1 (the printable-run extractor). Maximum
recursion depth is **3**; a deeper nesting is treated as the payload
itself rather than as another encoded layer, on the basis that no
legitimate use case stacks 4+ encodings.

### Step 4 — Run full skillaudit catalog on extracted text

Concatenate all extracted strings (from every depth) into a single
synthetic "extracted text" buffer and feed it to the existing
`cpv_skillaudit_native.scan_content` pipeline. All 50 rules / 489 patterns
fire normally — prompt-injection NL patterns, hardcoded-secret regexes,
suspicious-URL detectors, the lot.

### Step 5 — Emit findings with `[extracted from binary]` prefix

Every finding emitted from the extracted buffer gets its message prefixed
with `[extracted from binary]` so the reviewer can tell that the
underlying byte location was non-textual. Line numbers in extracted text
do NOT correspond to byte offsets in the binary — the prefix is the
reviewer's signal to inspect the binary with `strings` / `xxd` rather
than open it in a text editor.

## Edge cases (ALL logged, NEVER silent)

The "never skip a file" rule means every degenerate case must be reported,
never swallowed. The seven cases below are each emitted as their own
finding:

| Case | Finding severity | Note |
|---|---|---|
| File > 100 MB | INFO + scan completes | Chunked streaming in 4 MB windows; total memory bounded to one window |
| Permission denied | **WARNING** | Never skipped silently — a file the scanner cannot read is a file an attacker may have hidden |
| Zero-byte | INFO | "No findings (empty file)" — recorded so a reviewer can confirm the file is actually empty |
| High-entropy random bytes (no extractable strings ≥ 6 chars) | INFO | "No extractable strings — likely encrypted / compressed / random" |
| Decode-bomb (zlib-bomb, nested base64 that explodes) | INFO + cap enforced | 100 MB output cap per decode step; oversized output is truncated and a finding records the truncation |
| Recursion-depth-3 hit (still encoded after 3 layers) | INFO | "Decode chain stopped at depth 3 — deeper nesting treated as opaque payload" |
| Decoder error mid-stream (invalid base64 padding, corrupt gzip) | INFO | The decoder error itself becomes a finding so the reviewer can investigate why a string that LOOKED encoded did not decode |

The chunked-streaming guarantee for > 100 MB files means CPV never loads
a multi-gigabyte payload entirely into memory — string extraction
operates on a sliding 4 MB window with a 64-byte carry-over so strings
that straddle a chunk boundary still get captured.

## Env-var

```bash
CPV_BINARY_SCAN=0
```

Falls back to the legacy "skip binary files" behavior. **DEBUG ONLY** —
this knob exists so a maintainer can bisect a binary-scan regression
against the pre-v2.104.0 baseline. It MUST NEVER be set in CI, in
production validate gates, in scaffolded `publish.py`, or in any
unattended workflow. The CPV self-scan refuses to publish a release with
`CPV_BINARY_SCAN=0` set in any shipped workflow file — the only legal
caller is a local maintainer's interactive shell during triage.

There is intentionally no per-file or per-extension opt-out. The skip
gate is binary (whole feature on / whole feature off) and the OFF mode
exists only for diagnostic bisection.

## What this scanner does NOT do

This scanner intentionally stops at "extract strings + decode chain + run
the existing text-mode catalog." It does NOT do:

| Out of scope | Why | Use instead |
|---|---|---|
| Disassembly (`objdump` / `nm` / `radare2` / `ghidra`) | Adds a heavy native dependency and pulls CPV into the offensive-security tooling space, which is a different threat model | `radare2`, `Ghidra`, `IDA Pro` for binary RE work |
| Symbolic execution | Same as above — different threat model, different tool category | `angr`, `manticore` for symbolic execution audits |
| Crypto side-channel detection | Requires runtime tracing, not static analysis | `oss-fuzz`, `valgrind`, `kcc` |
| ML-based binary classification | Heavy model dependency, opaque false-positive rate, hard to audit | Out of scope for static-analysis CPV |
| Code-cave / PE-header anomaly detection | Antivirus / EDR territory | `clamav`, `yara` |

For offensive-security audits that need these capabilities, use
`cc-audit`, `semgrep`, or a dedicated binary RE tool. CPV's job is to
ensure the FULL string and decode space of every shipped artifact is
swept by the same rules that catch text-mode threats — nothing more,
nothing less.

## Coverage gain vs current text-only behavior

Classes of findings that surface in v2.104.0 that did NOT surface in
v2.103.x and earlier:

- **Hardcoded URLs / secrets in compiled binaries** — every `https://…`,
  AWS access key, GitHub token, JWT, PEM-encoded private key, OpenAI /
  Anthropic / OpenRouter API key in a `.so` / `.dll` / `.exe` / `.dylib`
  string table now triggers the same `HARDCODED_SECRET` rule that fires
  on plaintext source.
- **Embedded payloads in image EXIF** — base64-encoded shell commands,
  GitHub PATs, Anthropic API keys embedded as PNG `tEXt` / JPEG `COM`
  chunks are decoded and scanned.
- **Base64-encoded shell commands in JSON / YAML / PDF** — the decode
  chain unwraps the base64 wrapper before applying `CMD_INJECTION` and
  `PROMPT_INJECT` rules to the inner payload.
- **Source-code constants surviving Python `.pyc` compilation** — every
  string literal in the `.py` source is preserved verbatim in the `.pyc`
  `co_consts` table; the scanner extracts them and the full
  `INTENT_DESTRUCTIVE_INTENT`, `RECONNAISSANCE`, and `CREDENTIAL_REFERENCE`
  rule families fire normally.
- **WASM imports / exports / suspicious URLs** — the WASM binary format
  stores names as plain ASCII; suspicious-domain rules fire on extracted
  import host names.
- **Minified-bundle supply-chain markers** — the
  eval-of-base64-decode and Function-constructor-of-base64-decode
  patterns common to obfuscated supply-chain malware land in extracted
  strings before the base64 decode chain even runs, so the existing
  `EVAL_OBFUSCATION` rule fires regardless of how the dropper packages
  itself.

The total rule catalog (50 rules / 489 patterns) is unchanged — v2.104.0
only widens the INPUT surface those rules see.

## Performance characteristics

- **Text files** — unaffected. Detection (8 KB null-byte sniff) costs
  microseconds per file; non-binary files take the existing fast path.
- **Binary files** — typically 10-50 ms each depending on size and how
  many decode-chain layers actually fire. A 50 MB minified
  `vendor.bundle.js` lands near the upper end (single-pass string extract +
  one base64 round-trip + skillaudit catalog scan); a 100 KB compiled
  `.so` is at the lower end (single-pass extract, no encoded payloads,
  catalog scan on a few hundred extracted strings).
- **Compounds with parallelism** — every binary file runs in its own
  `ProcessPoolExecutor` worker via the existing
  `cpv_parallel_runner.parallel_scan` harness; binary scanning inherits
  the v2.103.0 fan-out automatically. A repo with 200 binaries on an
  8-core host finishes binary scanning in roughly `200 × 30 ms / 7` ≈
  860 ms wall time.
- **Memory** — bounded to one 4 MB chunk per worker for files > 100 MB;
  smaller files load fully (still well under 100 MB per worker).

The benchmark script `scripts/cpv_validate_benchmark.py` records the
binary-scan contribution as its own component breakdown row so a future
regression on binary scanning shows up against the v2.104.0 baseline
without contaminating the text-mode numbers.

## Future work

Deferred to later versions, called out here so the design space is
captured:

- **Steganographic image analysis** — LSB extraction, DCT coefficient
  anomaly detection. Currently only EXIF / metadata chunks are scanned.
- **Native disassembly integration** — optional Ghidra / radare2 hook for
  installations that want the full RE pipeline. Would ship as a separate
  CPV plugin to keep the core dependency-free.
- **ML-based binary classification** — flag binaries whose extracted
  string distribution looks anomalous compared to a corpus baseline.
  Requires audit infrastructure for the model itself; deferred.
- **`.pyc` bytecode disassembly** — currently `co_consts` strings are
  extracted but `co_code` opcodes are not analyzed. Future work could
  reconstruct the AST and run the existing Python-context classifier on
  the reconstructed source.
- **PE / ELF / Mach-O header parsing** — surface imports, exports, and
  section names as their own structured findings rather than relying on
  string extraction to catch them.

None of these are required for the v2.104.0 commitment ("never skip a
file"). They are coverage expansions on top of the binary-aware baseline
this document defines.
