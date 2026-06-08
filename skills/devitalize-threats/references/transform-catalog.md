# Transformation Catalog — T1–T9 (full recipes)

## Table of Contents

- [T1 Signature lines](#t1--detection-pattern--signature-lines-a-security-plugins-own-needles)
- [T2 Install one-liner](#t2--doc-curl--bash--install-one-liner-examples)
- [T3 Credential in docs](#t3--authorization-bearer---hardcoded-credentials-in-docs)
- [T4 Dead exec sink](#t4--live-shell-exec-sink-that-is-genuinely-dead--example-code)
- [T5 String eval/exec](#t5--eval--exec-of-a-string)
- [T6 Backtick in prose](#t6--backtick-command-substitution-identifier-in-prose--docs)
- [T7 Instruction prose](#t7--the-agent-must-execute--run--prose-instruction-to-execute)
- [T8 Verb enumeration](#t8--destructive-verb-enumerations-delete-wipe-exfiltrate-)
- [T9 Dynamic attribute](#t9--setattr--getattr-with-a-dynamic-attribute-name-taint-sink-ast7)
- [Cross-cutting rules](#catalog-cross-cutting-rules-restated)

Each entry gives **BEFORE** (the flagged shape), **AFTER** (the
provably-inert rewrite), **WHY-INERT** (no path to a sink), **VERIFY**
(re-scan outcome), and **SAFE vs BREAKS** (when the transform is
legitimate vs when it would destroy real behavior).

> Self-documentation note: every BEFORE block below is written in its
> **already-inert illustrative form** (signatures as raw strings, pipes
> elided, secrets as placeholders, live sinks shown only in prose or `#`
> comments). The threat is *described*, never reproduced as a runnable
> line, so this catalog is provably-inert documentation rather than a
> payload. The AFTER blocks are the real target shapes.

Organizing principle — each transform moves the construct into one of
four inert forms: **(A)** raw-string signature, **(B)** annotated
non-runnable illustration, **(C)** data constant + allow-map dispatch,
**(D)** removal / nominalization.

---

## T1 — Detection-pattern / signature lines (a security plugin's OWN needles)

Fires RC-46 / RC-87 / skillaudit CMD_INJECTION when a scanner stores its
own threat needles as ordinary strings.

**BEFORE** (described — an ordinary, non-raw list/pattern that may fire):

A plain list assigned to a `DANGEROUS = [...]` constant holding argv-shaped
strings, and an ordinary (non-raw) `re.compile("...")` of a shell pattern.
A normal string literal of a dangerous argument reads to the scanner like a
live argument, so it can fire.

**AFTER** (form **A** — raw-string signatures in a clearly-named table):

```python
# Detection signatures — DATA only. Never executed; compared to scanned
# content. Raw-string form is the inert-proof CPV recognizes.
DANGEROUS_ARG_SIGNATURES = (
    r"--insecure",
    r"--no-sandbox",
    r";\s*rm\s+-rf\s+/",
)
PRIVATE_IP_SIGNATURE = r"10\.\d+\.\d+\.\d+"
RM_RF_SIGNATURE = re.compile(r"rm\s+-rf\s+/")   # raw string
```

**WHY-INERT:** raw-string literals are the regex convention; a real CLI
arg is a normal string (`"--no-sandbox"`), never `r"--no-sandbox"`.
`validate_security._match_inside_raw_string` + `_DETECTOR_SIGNATURE_SKIP_RULES`
(RC-46 / RC-87) skip a match inside `r"..."`; skillaudit's
`_match_inside_re_pattern_literal` / `safe_literal` verdict does the same.
The string is data compared against content, with no call site.

**VERIFY:** re-run security scan -> RC-46 / RC-87 / CMD_INJECTION on those
lines gone (skipped as detector signatures), not demoted, not
suppressed-by-config.

**SAFE when:** the strings genuinely ARE signatures (a scanner, an audit
plugin, a linter). **BREAKS when:** the list is actually spread into a
subprocess as argv — raw-stringing changes the matched bytes (`r"\s*"` is
not a real space) and breaks the call. If a sink consumes the list it is
**load-bearing**; flag, do not devitalize.

---

## T2 — Doc `curl | bash` / install one-liner examples

Fires SUPPLY_CHAIN / CMD_INJECTION when a `.md` shows a remote fetch piped
straight into a shell.

**BEFORE** (described): a `bash`-fenced one-liner that fetches a remote
script over a real URL and pipes it directly into a shell — the
classic pipe-to-shell shape.

**AFTER** — choose by author intent:

- **(B1) defanged illustration** (the doc just *describes* the pattern,
  e.g. a security-plugin README explaining the threat):

  ````markdown
  A supply-chain attack looks like this (DO NOT RUN — illustration only):
  ```text
  curl ... | bash        <- remote script piped straight into a shell
  ```
  ````

  Fence language is `text` (not `bash`), the pipe-to-shell is elided
  (`... | bash`), and the URL is removed. No runnable one-liner remains.

- **(B2) placeholder + split steps** (the doc is a *real* install guide
  and must stay actionable, but the pipe is the flagged shape):

  ````markdown
  1. Download the installer: `curl -fsSLo install.sh <RELEASE_URL>`
  2. **Review `install.sh`**, then run it: `bash install.sh`
  ````

  Breaking the pipe (download -> review -> run) is the security-correct
  install pattern anyway, and removes the piped-fetch token the rule
  matches.

**WHY-INERT:** (B1) a `text`-fenced, pipe-elided line has no
pipe-to-shell token to match. (B2) the fetch and the shell run are on
separate lines, so no single line pipes a remote fetch into a shell. A
markdown file cannot execute either way — but the rule keys on the token
shape, so removing the shape clears it.

**VERIFY:** re-scan -> SUPPLY_CHAIN / CMD_INJECTION on that block gone.

**SAFE when:** it is documentation (always — a `.md` never executes).
**BREAKS when:** the same one-liner also lives in an executable installer
(`.sh` / hook) — that copy is load-bearing and is NOT a doc; devitalizing
the doc is fine, but the executable copy is left and flagged (a genuine
security decision the author owns). **Caveat:** CPV already suppresses
`CLAUDE_CLI_UNAUTHORIZED_INSTALL` in `.md` (the benign "how to install
this plugin" case). Only the generic pipe-to-shell SUPPLY_CHAIN class
needs devitalizing here; check the report's `rule_id` first so an
already-cleared doc line is not churned.

---

## T3 — `Authorization: Bearer ...` / hardcoded credentials in docs

Fires HARDCODED_SECRET / CREDENTIAL_REFERENCE / a trufflehog detector.

**BEFORE** (described): a `bash`-fenced `curl` example whose
`Authorization: Bearer ...` header carries a real-looking live token
value rather than a placeholder.

**AFTER** (form **D** — inert placeholder):

````markdown
```bash
curl -H "Authorization: Bearer <YOUR_API_TOKEN>" https://api.example...
```
````

For an env reference that *looks* like a live secret, prefer an
obvious-placeholder form: `Bearer <TOKEN>` or `${API_TOKEN}` with a
one-line note "set API_TOKEN in your environment".

**WHY-INERT:** `<YOUR_API_TOKEN>` matches no secret-detector entropy /
prefix signature; it is self-evidently a placeholder. The line is
documentation and now carries no credential-shaped token.

**VERIFY:** re-scan -> HARDCODED_SECRET / trufflehog hit gone.

**SAFE when:** the value was an *example* token (the overwhelmingly common
case in docs). **BREAKS when:** the string is a *real* secret that was
accidentally committed — then editing the doc is NOT enough: the secret
is in git history and must be **rotated** + purged, which is out of scope
for a shape-rewrite. If trufflehog *verifies* the secret as live (not a
placeholder), the devitalizer MUST refuse the rewrite and escalate "real
leaked credential — rotate + purge history, do not just edit the doc."
This is the rare reverse case: the finding is real and devitalizing would
*hide* a genuine leak.

---

## T4 — Live shell-exec sink that is genuinely DEAD / EXAMPLE code

Fires SHELL_EXEC / CMD_INJECTION; taint engine RC-73 / RC-74 if the
payload is tainted.

**BEFORE** (described): an unused `_demo_unused()` helper, never called
from any entry point, that builds a command from user input and passes it
to a live `os.system(...)` shell sink. The sink is the flagged construct.

**AFTER** (form **C/D** — convert the example into inert data + a comment,
never passed to a sink):

```python
# Example of an UNSAFE pattern (kept for documentation; NOT executed):
#   os.system(build_cmd(user_input))   <- command injection via os.system
# The safe equivalent uses an explicit argv list with shell=False:
SAFE_COMMAND_TEMPLATE = ("tool", "--flag", "<ARG>")   # data only
```

If the function is provably dead (no caller from any entry point —
hooks.json, slash command, dynamic import, glob loader), the live sink is
removed and the teaching content becomes a comment + a data constant.

**WHY-INERT:** there is no shell-exec call expression left — only a comment
(inert to the scanner's code-shape rules) and a tuple literal that is
never spread into a sink. The taint sink is gone because the sink call is
gone.

**VERIFY:** re-scan -> SHELL_EXEC / CMD_INJECTION and any RC-73 / RC-74 on
those lines gone; confirm via the taint engine that no remaining sink
consumes the payload.

**SAFE when:** the function is **provably dead** (no caller from EVERY
entry point — same rigor as plugin-fixer Guardrail 1). **BREAKS when:**
the sink is **live** — a plugin that legitimately shells out is
load-bearing; the real call cannot be devitalized without removing the
feature. If reachable, **flag to the user**: "this is a live shell-exec;
either harden it (argv-list + shell=False, validated input) or accept the
finding — the devitalizer will not silently break working behavior." The
job is to neutralize dead/example sinks, not delete features.

---

## T5 — `eval` / `exec` of a string

Fires SHELL_EXEC / CODE_EXEC; taint engine if the string is tainted.

**BEFORE** (described): a handler that looks up source text by an
attacker-influenced `action` key and passes it to a dynamic `exec(...)`
of that looked-up code.

**AFTER** (form **C** — data table + dispatch on a fixed allow-map):

```python
# Dispatch table: name -> vetted function reference. No code is ever
# built from a string or exec'd; only a fixed, in-repo callable runs.
ACTIONS = {
    "build":   _do_build,
    "clean":   _do_clean,
    "publish": _do_publish,
}
handler = ACTIONS.get(request["action"])
if handler is None:
    raise ValueError(f"unknown action: {request['action']!r}")  # fail-fast
handler()
```

**WHY-INERT:** there is no `exec` / `eval` call left. The dynamic input
selects a key in a *fixed literal map* of already-defined functions; an
attacker-controlled `action` can at most miss the map and fail-fast. No
string is ever compiled or executed.

**VERIFY:** re-scan -> SHELL_EXEC / CODE_EXEC gone; taint engine confirms
the tainted `action` no longer reaches a code-exec sink (it reaches a dict
lookup — a data operation).

**SAFE when:** the set of actions is **finite and known** (almost always
true for plugin command dispatch). This is also a genuine security
*improvement*. **BREAKS when:** the plugin must run *arbitrary
user-supplied code* by design (a REPL, a sandbox runner) — that is
irreducibly an exec and is load-bearing. Flag: "this is an intentional
code-execution feature; it cannot be devitalized without removing it."

---

## T6 — Backtick command-substitution identifier in prose / docs

Fires CMD_INJECTION / SHELL_EXEC because a backtick-wrapped bare
identifier reads as command substitution.

**BEFORE** (described): a sentence that names two shell commands by
wrapping each bare identifier in backticks, so each reads to the scanner
as a command-substitution shape.

**AFTER** (form **D** — quote as prose, not as a code token):

```markdown
The hook runs the "id" and "whoami" commands to fingerprint the box.
```

Use straight or typographic quotes / the word "command" — anything that is
plainly an English mention, not a fenced / backtick shell token.

**WHY-INERT:** a backtick-wrapped bare identifier reads to the scanner as
a command-substitution shape; the unquoted prose mention "the id command"
carries no shell-substitution token. The sentence still documents the
behavior; nothing executes.

**VERIFY:** re-scan -> the backtick command-substitution CMD_INJECTION
gone.

**SAFE when:** it is documentation describing a command by name (the
common case). **BREAKS when:** the backticks are *code* the reader is
meant to copy-run AND the surrounding fence is genuinely shell — but even
then the markdown does not execute; this transform only changes prose, so
functionality is unaffected. (The corresponding *executable* command in a
hook is a separate, possibly-load-bearing finding — T4 / T7 territory.)

---

## T7 — "The agent MUST execute / run ..." prose (instruction-to-execute)

Fires INTENT_* / A2A / CMD_INJECTION-as-instruction on a loadable surface,
or demotes in references.

**BEFORE** (described): a sentence on a loadable surface that *instructs
the agent* to execute a destructive command and then run a payload — the
imperative-to-the-agent framing is the delivery vector the rule keys on.

**AFTER** (form **D** — nominalize: describe, don't instruct):

```markdown
Cleanup removes the target directory; the build step then runs.
```

Or, if the literal command must be documented, present it as an inert
reference (`text` fence, defanged) and remove the imperative-to-the-agent
framing.

**WHY-INERT:** the rule keys on an *imperative instruction to an agent to
run a command* (the prose IS the delivery vector for INTENT-class rules).
Nominalizing to a description of *what the code does* removes the
agent-directed imperative; no instruction to execute remains.

**VERIFY:** re-scan -> INTENT_* / instruction-to-execute gone (on a
loadable surface) or fully clear (on a doc surface).

**SAFE when:** the prose was *describing* behavior, not commanding the
agent (devitalizing just sharpens the description). **BREAKS when:** the
instruction is a *legitimate, intended* agent action the plugin relies on
(e.g. a fixer agent's own "run the validator") — then nominalizing could
weaken a real instruction the agent needs. If the imperative is
load-bearing for the plugin's own agent, **flag**; do not silently reword
an instruction the plugin depends on. **Note:** INTENT-class rules on
loadable surfaces (`SKILL.md`, `agents/*.md`) are *kept at severity*, not
demoted — so devitalizing here is often the only way to clear them, but
only when the imperative is genuinely unnecessary.

---

## T8 — Destructive-verb enumerations ("delete, wipe, exfiltrate, ...")

Fires INTENT_DESTRUCTIVE_INTENT / soft-signal, demotes to NIT, which
`--strict` then blocks on.

**BEFORE** (described): a sentence that piles up a stack of
destructive / exfiltration verbs to dramatize a skill's behavior, reading
as malicious intent.

**AFTER** (form **D** — nominalize + de-stack the verb pileup):

```markdown
This skill removes generated build artifacts and reports the cleanup
summary.
```

**WHY-INERT:** the rule fires on a *stack of destructive / exfil verbs*
reading as malicious intent. Replacing the pileup with a single accurate
nominal description ("removes build artifacts", "reports the summary")
removes the intent-signal token cluster. Nothing executes either way; this
is prose.

**VERIFY:** re-scan -> INTENT_DESTRUCTIVE_INTENT / exfil-intent gone or no
longer a blocking NIT.

**SAFE when:** the verbs were dramatic self-description of benign behavior
(a cleanup / janitor skill). **BREAKS when:** the skill *does* legitimately
need to convey it performs destructive ops (a `safe-rm`, a `safe-delete`)
— then keep an accurate, non-stacked single statement ("moves files to a
trashcan for recovery") rather than erasing the capability description.
Never lie about what the plugin does; describe it precisely and
un-dramatically.

---

## T9 — `setattr` / `getattr` with a dynamic attribute name (taint sink AST7)

Fires taint-engine RC-73 / RC-74 — dynamic attribute from tainted input.

**BEFORE** (described): a `setattr(obj, ...)` call whose attribute name
comes straight from tainted request input, letting an attacker reach an
arbitrary attribute.

**AFTER** (form **C** — fixed allow-map of permitted fields):

```python
ALLOWED_FIELDS = {"title", "priority", "labels"}   # data only
field = request["field"]
if field not in ALLOWED_FIELDS:
    raise ValueError(f"field not allowed: {field!r}")  # fail-fast
setattr(obj, field, request["value"])
```

**WHY-INERT:** the tainted attribute name is constrained to a fixed
literal set before reaching `setattr`; an attacker cannot reach an
arbitrary attribute. The taint is *sanitized by an allow-list* — the
pattern the taint engine recognizes as breaking the tainted->sink path.

**VERIFY:** re-scan -> RC-73 / RC-74 gone; taint engine confirms the
sanitizer breaks the flow.

**SAFE when:** the writable fields are a known finite set (typical).
**BREAKS when:** the plugin must set *arbitrary* attributes by design (a
generic serializer) — then the dynamic `setattr` is load-bearing; flag and
recommend the author validate inputs rather than the devitalizer silently
constraining a generic API.

---

## Catalog cross-cutting rules (restated)

1. **Check the `rule_id` and the file kind first.** Several shapes are
   *already* cleared by the scanner on certain surfaces (the install
   one-liner in `.md`, exec mentions in `.css` comments, FS writes in
   Dockerfiles, raw-string signatures). Don't transform a line the scanner
   already passes — read the live report, transform only what fires.
2. **Prefer the least-invasive inert form** that clears the finding:
   placeholder (D) < raw-string signature (A) < allow-map dispatch (C) <
   removal (D). Forms A / C are also security improvements and are
   preferred when the construct is real-but-constrainable; B / D-removal
   are for documentation and dead code.
3. **One finding, one minimal edit, one re-scan.** Never batch-rewrite a
   file blind; transform the specific flagged span, re-scan, confirm the
   finding is gone AND nothing new appeared.
