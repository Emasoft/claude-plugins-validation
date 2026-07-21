# Harden Recipes — Part C (full safeguard recipes)

## Table of Contents

- [C1 launch / deploy params](#c1--safe-launch--deploy--network-parameters)
- [C2 safe config parse](#c2--safe-config-parsing-per-format)
- [C3 input sanitization](#c3--input-sanitization)
- [C4 safe file load](#c4--safe-file-loading)
- [C5 prompt-injection pre-scan](#c5--by-code-only-prompt-injection-pre-scan-the-pre-read-guard)
- [Cross-cutting rules](#cross-cutting-rules-restated)

Each entry gives **BEFORE** (the missing-safeguard shape, described),
**AFTER** (the hardened rewrite), **WHY-SAFE** (the safeguard closes the
hole), **VERIFY** (re-scan outcome), and **NO-FUNCTIONALITY-LOSS** (the
safe form accepts the same valid inputs).

> Self-documentation note: every BEFORE block below is written in its
> **already-inert form** — the unsafe shape (a full YAML loader, an
> untrusted deserializer, a disabled-verification flag, an SSRF-prone
> fetch) is *described* in prose or a `#` comment, never reproduced as a
> runnable line. The AFTER blocks are the real, safe target shapes. This
> keeps the catalog itself free of the very shapes it teaches against.

The C contract restated: **add the missing safeguard, or flag it — never
weaken the scan.** The only acceptable clear is the scanner no longer
firing because the safeguard now exists.

---

## C1 — Safe launch / deploy / network parameters

Fires INSECURE_TLS, SSRF_ADVANCED / SSRF_PATTERN, DNS_REBIND, RC-61
(`dangerouslyDisableSandbox`), RC-62 (`permissionMode: bypassPermissions`),
or a subprocess-shape rule when a tool is launched, a service deployed, or
a request issued without the standard guards.

**BEFORE** (described): a network client constructed with certificate
verification turned off; a fetch that takes a user-supplied URL and follows
it to any host (SSRF); a request that resolves a host once and trusts a
later changed answer (DNS rebinding); a launch flag that disables the
sandbox or bypasses permissions; a subprocess that runs a string through a
shell. Each is a missing guard, described — not a runnable unsafe line.

**AFTER** — TLS verification ON (it is the default; never pass a
false value to the verify flag):

```python
import requests

# verify defaults to True; the safe form simply does not disable it.
resp = requests.get("https://api.example.invalid/v1/status", timeout=10)
```

**AFTER** — SSRF guard (allow-list + block private / loopback /
link-local before fetching a user-supplied host):

```python
import ipaddress
import socket

ALLOWED_HOSTS = {"api.example.invalid", "cdn.example.invalid"}

def assert_safe_host(host: str) -> None:
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"host not allowed: {host!r}")
    addr = ipaddress.ip_address(socket.gethostbyname(host))
    if addr.is_private or addr.is_loopback or addr.is_link_local:
        raise ValueError(f"refusing internal address for {host!r}")
```

**AFTER** — DNS-rebind pin (resolve once, then connect to the pinned IP so
a changed DNS answer cannot redirect the connection):

```python
import socket

def resolve_and_pin(host: str) -> str:
    pinned_ip = socket.gethostbyname(host)   # resolve once
    assert_safe_host(host)                    # validate the resolved addr
    return pinned_ip                          # connect to this IP, not re-resolve
```

**AFTER** — sandbox / permission flags: remove the downgrade *only when it
is unnecessary*. Run the tool with its default sandbox on and its default
permission mode; do not pass a flag that disables the sandbox or bypasses
permissions.

**Load-bearing exception (flag, don't break):** `bypassPermissions` is a
VALID permission mode and a disabled sandbox can be a tool's intended
function (an automation plugin that must run without permission prompts).
If the downgrade is load-bearing — the plugin's documented purpose requires
running without the sandbox / permission gate — do NOT remove it. **FLAG it**
for an explicit user decision, per the Q2-safeguard gate and cross-cutting
rule 4 (flag, don't break). Removal is correct only when no feature depends
on the downgrade. Cross-ref `cpv-devitalize-threats` (the load-bearing-triage
live + irreducible row) for the dead-vs-live split.

**AFTER** — subprocess with an argv list and the shell disabled:

```python
import subprocess

subprocess.run(["tool", "--flag", user_arg], shell=False, check=True)
```

**WHY-SAFE:** verification-on rejects forged certificates; the allow-list
plus a private-IP block stops a user-supplied URL from reaching internal
services; the DNS pin removes the rebind window; default sandbox /
permissions keep the tool contained; an argv list with the shell disabled
means a malicious `user_arg` is a single argument, never a shell command.

**VERIFY:** re-scan → INSECURE_TLS / SSRF_* / DNS_REBIND / RC-61 / RC-62 /
the subprocess-shape finding on those lines is gone.

**NO-FUNCTIONALITY-LOSS:** legitimate hosts on the allow-list still
resolve and connect; valid certificates still verify; the tool runs the
same command via an argv list. The only behavior removed is the unsafe
path (forged certs, internal hosts, shell metacharacters). Cross-ref
`cpv-devitalize-threats` T4 / T5 for the dead-vs-live split when the subprocess
sink is example / dead code rather than a live path to harden.

---

## C2 — Safe config parsing (per format)

Fires DESERIALIZATION, XXE_INJECTION, or an RC firing on an unsafe parser
when config / data is read with a loader that can construct arbitrary
objects or resolve external entities.

**BEFORE** (described): YAML read with the *full* loader (which can
construct arbitrary Python objects from untrusted text); XML parsed with
external-entity resolution left on (XXE); JSON read with no size bound
(memory-exhaustion on a hostile file); a cfg/ini parser with surprising
interpolation; a plist read without a pinned format. Each is described, not
shown as a runnable unsafe call.

**AFTER** — the safe loader per format:

```python
import json
import tomllib
import configparser
import plistlib
import yaml                                  # PyYAML
from defusedxml.ElementTree import parse as safe_xml_parse

# YAML: the safe loader constructs only basic types, never arbitrary objects.
data = yaml.safe_load(open("config.yaml", encoding="utf-8"))

# TOML: the stdlib parser (3.11+) is safe by construction.
with open("config.toml", "rb") as f:
    toml_data = tomllib.load(f)

# JSON: cap the size before parsing to bound memory.
raw = open("data.json", encoding="utf-8").read(1_000_000)   # 1 MB cap
json_data = json.loads(raw)

# cfg / ini: disable interpolation to avoid surprises.
cp = configparser.ConfigParser(interpolation=None)
cp.read("settings.ini", encoding="utf-8")

# plist: pin the format.
with open("Info.plist", "rb") as f:
    plist_data = plistlib.load(f, fmt=plistlib.FMT_XML)

# XML: defusedxml disables external entities and entity expansion (XXE).
tree = safe_xml_parse("data.xml")
```

**WHY-SAFE:** the YAML safe loader constructs only basic scalars and
containers, so untrusted text cannot instantiate arbitrary objects; the
stdlib TOML parser has no object-construction surface; the size cap bounds
JSON memory; disabling interpolation removes the cfg/ini surprise; a pinned
plist format avoids ambiguous parsing; defusedxml refuses external entities
and billion-laughs expansion, closing XXE.

**VERIFY:** re-scan → DESERIALIZATION / XXE_INJECTION / the unsafe-parse
finding on those lines is gone.

**NO-FUNCTIONALITY-LOSS:** every *valid* config document still parses to
the same data — the safe loaders accept the same well-formed inputs and
only reject the dangerous constructs (arbitrary object tags, external
entities, unbounded payloads) that a correct config never contains.

---

## C3 — Input sanitization

Fires SQL_INJECTION, XSS_INJECTION, REGEX_DOS, PROTOTYPE_POLLUTION, or
LOG_INJECTION when runtime input reaches a query, a page, a regex, an
object merge, or a log line without sanitization.

**BEFORE** (described): a SQL query built by string-concatenating user
input; user input written into HTML without encoding (XSS); a
user-supplied or catastrophic-backtracking regex run on unbounded input
(ReDoS); an untrusted object merged into a target (prototype pollution); a
user value written raw into a log (log injection). Each is described, not
shown as a runnable unsafe line.

**AFTER** — parameterized SQL (the driver binds values; they never become
SQL syntax):

```python
cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
```

**AFTER** — output-encode for XSS (encode before placing user data in
HTML):

```python
import html

safe_fragment = html.escape(user_input)   # & < > " ' -> entities
```

**AFTER** — bounded regex + length cap for ReDoS (cap input length; use a
linear pattern; prefer the re2 engine when available):

```python
MAX_LEN = 1000
if len(candidate) > MAX_LEN:
    raise ValueError("input too long")
# Avoid nested quantifiers; a linear pattern + a length cap removes the
# backtracking blow-up. With google-re2 installed, use re2 for a linear
# guarantee on the matcher itself.
match = bounded_pattern.match(candidate)
```

**AFTER** — allow-map for dynamic dispatch (the dynamic key selects a
vetted callable; nothing is built from a string):

```python
ACTIONS = {"build": _do_build, "clean": _do_clean}   # data only
handler = ACTIONS.get(request["action"])
if handler is None:
    raise ValueError(f"unknown action: {request['action']!r}")
handler()
```

**AFTER** — prototype-pollution guard (reject the dangerous keys before
merging untrusted data):

```javascript
const FORBIDDEN = new Set(["__proto__", "constructor", "prototype"]);
for (const key of Object.keys(incoming)) {
  if (FORBIDDEN.has(key)) {
    throw new Error(`forbidden key: ${key}`);
  }
  target[key] = incoming[key];
}
```

**AFTER** — log sanitization (strip control characters / newlines so a
user value cannot forge log lines):

```python
def log_safe(value: str) -> str:
    return value.replace("\n", " ").replace("\r", " ")
```

**WHY-SAFE:** parameter binding keeps user data out of SQL syntax;
output-encoding keeps it out of HTML markup; the length cap + linear
pattern removes the backtracking blow-up; the allow-map keeps a dynamic
key out of any code-build; the forbidden-key check stops `__proto__` from
polluting the prototype; stripping control characters stops forged log
lines.

**VERIFY:** re-scan → SQL_INJECTION / XSS_INJECTION / REGEX_DOS /
PROTOTYPE_POLLUTION / LOG_INJECTION on those lines is gone.

**NO-FUNCTIONALITY-LOSS:** legitimate values still query, render, match,
merge, and log correctly — the sanitization rejects only the injection
payloads (SQL metacharacters as syntax, HTML tags, pathological regex
input, prototype keys, control characters) that valid data never carries.

---

## C4 — Safe file loading

Fires DESERIALIZATION or PATH_TRAVERSAL when a file is loaded with an
unsafe deserializer or read from a user-controlled path.

**BEFORE** (described): a file loaded with a deserializer that executes
code on untrusted input (the pickle family on attacker-controlled bytes);
a path joined from user input with no containment, letting `..` segments
escape the intended directory (path traversal). Each is described, not
shown as a runnable unsafe call.

**AFTER** — no untrusted deserialization (use a data-only format for
untrusted input; the pickle family is never used on attacker-controlled
bytes):

```python
import json

# Untrusted payloads use a data-only format that cannot execute code.
record = json.loads(untrusted_bytes.decode("utf-8"))
```

**AFTER** — path containment (resolve the real path and confirm it stays
under the base directory):

```python
import os

def safe_join(base_dir: str, user_path: str) -> str:
    base = os.path.realpath(base_dir)
    candidate = os.path.realpath(os.path.join(base, user_path))
    if not candidate.startswith(base + os.sep) and candidate != base:
        raise ValueError(f"path escapes base directory: {user_path!r}")
    return candidate
```

For large files, stream in chunks rather than reading the whole file into
memory.

**WHY-SAFE:** a data-only format cannot construct or execute code, so an
untrusted payload is inert data; the realpath containment check resolves
symlinks and `..` segments and rejects any path that escapes the base
directory, closing traversal.

**VERIFY:** re-scan → DESERIALIZATION / PATH_TRAVERSAL on those lines is
gone.

**NO-FUNCTIONALITY-LOSS:** valid payloads still load (as data); legitimate
paths under the base directory still resolve; only out-of-tree paths and
code-bearing deserialization are rejected.

---

## C5 — By-code-only prompt-injection pre-scan (the pre-read guard)

PROMPT_INJECT / INDIRECT_PROMPT_INJECT (RC-127 / RC-128 and the native
skillaudit equivalents) fire on the **static prose of a plugin-shipped
file** — a SKILL.md, agent body, command `.md`, or reference doc that
contains an injection phrase. The scanner reads file CONTENT; it has no
notion of a runtime read/fetch path. So C5 covers **two distinct cases
that need different fixes** — read both before editing:

- **(a) The finding is on the plugin's OWN shipped prose.** This is what
  the scanner actually flags. The fix is to **rephrase or fence the
  prose** (RC-127's own help text: "rephrase the documentation … wrap the
  example in backticks or a fenced code block"). A runtime guard does
  NOTHING for this — the static text still fires. This is the case that
  clears the finding.
- **(b) The plugin READS untrusted content at runtime** (a web page, a
  file, an issue body, a tool result) and feeds it to an agent. A
  pure-code (no-LLM) pre-read guard is **genuine hardening of the
  plugin's behavior** — it defends the plugin's own agent/skill flow
  against indirect injection. But it does NOT clear a static-prose
  finding, because no static finding fired on the runtime read in the
  first place. Add it for real defense, not to pass the scan.

The recipe below gives the case-(b) guard; case (a) is just rephrase /
fence the offending prose.

**BEFORE** (described): a plugin flow that fetches or reads untrusted
content and hands it straight to an agent / model with no pre-read check.
The untrusted text can carry injected instructions (an indirect prompt
injection) that the agent then obeys. Described — not a runnable injected
payload.

**AFTER** — a deterministic pre-read guard that scans for injection
markers and either neutralizes them or refuses, run BEFORE the agent sees
anything:

```python
import re

# Marker shapes the same as CPV's own prompt-injection rules look for —
# imperative system-prompt overrides, role-reassignment phrasings, and
# disregard-prior-context framings. DATA only: a list of detector needles
# compared against content, never executed. NOTE: the narration here
# deliberately ABSTRACTS the operative phrasings rather than quoting a live
# injection phrase verbatim — a quoted phrase in this comment would itself
# fire PROMPT_INJECT (per cpv-devitalize-threats T1). The regex literals below
# are the needles, and the scanner already treats a regex-pattern literal as
# inert data; only free prose that quotes the phrase verbatim would fire.
INJECTION_MARKERS = (
    r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions",
    r"disregard\s+(?:the\s+)?(?:system|previous)\s+prompt",
    r"you\s+are\s+now\s+(?:a|an)\b",
    r"new\s+instructions\s*:",
    r"</?(?:system|assistant|developer)>",
)
_MARKER_RE = re.compile("|".join(INJECTION_MARKERS), re.IGNORECASE)

def prescan_untrusted(text: str) -> tuple[bool, list[str]]:
    """Return (is_clean, matched_markers). Pure code, no model call."""
    hits = [m.group(0) for m in _MARKER_RE.finditer(text)]
    return (not hits, hits)

def read_untrusted_then_guard(raw: str):
    is_clean, hits = prescan_untrusted(raw)
    if not is_clean:
        # Refuse-and-flag: do NOT pass flagged content to the agent.
        raise ValueError(f"prompt-injection markers found: {hits!r}")
    return raw   # only clean content ever reaches the agent
```

Wire `read_untrusted_then_guard` as the ONLY entry point through which the
plugin's agent/skill flow obtains untrusted content — the fetch / file
read returns its bytes to this guard first, and the agent reads only the
guard's clean output.

**WHY-SAFE:** the guard is pure code that runs *first*, so no agent ever
reads raw untrusted content. Flagged content is refused (or, if the plugin
prefers, the markers are stripped and the result re-scanned) before it can
become part of any prompt. The marker list is DATA compared against
content — there is no execution sink, so the guard itself carries no
threat shape. The regex needles are stored as raw-string literals in a
clearly-named table, which the scanner already proves inert (a
regex-pattern literal, not a live argument) — so the guard's own marker
list does NOT fire PROMPT_INJECT.

**VERIFY (be precise about which case you are in):**

- *Case (a) — the finding is on the plugin's own shipped prose.* The fix
  is to **rephrase / fence the offending prose**; re-scan → that
  PROMPT_INJECT / INDIRECT_PROMPT_INJECT finding is gone. **Adding the
  runtime guard does NOT clear it** — a re-scan after adding the guard
  shows the static-prose finding UNCHANGED, because the scanner flags the
  text, not the read path. Do not attribute the clear to the guard.
- *Case (b) — the runtime-read defense.* Adding `read_untrusted_then_guard`
  is real hardening but clears NO static finding (none fired on the
  runtime read). Re-scan → finding counts unchanged; that is expected.
  Confirm the guard is on every untrusted-read path by code review, not by
  a scan delta.
- *Guard self-check (both cases):* after writing the guard, re-run at the
  same `--strict` level and confirm the guard's marker list and its
  surrounding prose did NOT introduce a new PROMPT_INJECT finding. If the
  guard's narration quotes a live injection phrase verbatim, it fires (and
  `--strict` blocks on it) — abstract the offending phrasing per
  `cpv-devitalize-threats` T1 (describe the phrasing, do not quote it).

**NO-FUNCTIONALITY-LOSS:** clean content passes through unchanged and the
flow behaves exactly as before; only content carrying injection markers is
refused (or neutralized), which is the intended hardening. This is the
user-emphasised safeguard: a BY-CODE-ONLY preventive scan that runs BEFORE
any agent reads untrusted content. (It is a hardening recipe for the
plugin under repair, not a new CPV detector — it adds no entry to CPV's
own rule catalog.)

---

## Cross-cutting rules (restated)

1. **Check the `rule_id` and file kind first.** A write already
   context-suppressed in a Dockerfile, a parse already on the safe loader,
   a path that never sees untrusted input — read the live report and
   harden only what actually fires.
2. **Add the single missing safeguard.** Prefer the least-invasive safe
   form (the safe loader, the one guard, the containment check) over a
   refactor; preserve the same valid inputs and outputs.
3. **One finding, one minimal edit, one re-scan.** Harden the specific
   flagged span, re-scan, confirm the finding is gone AND no new finding
   appeared.
4. **Flag, don't break.** A safeguard that would change behavior, or a
   path that never sees untrusted input, is FLAGGED with the reasoning —
   never rewritten into a guard that rejects legitimate data.
