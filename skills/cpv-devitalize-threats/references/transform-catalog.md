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
- [Irreversibility test](#irreversibility-test)
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

**AFTER** (form **A** — raw-string signatures in a clearly-named table,
each one a comparison needle fed to `re.<func>` or held in a DATA-only
table, with NO call site that spreads it into a shell or argv):

```python
# Detection signatures — DATA only. Compared to scanned content via
# re.search / membership; never spread into a shell or argv. Raw-string
# form is the regex convention CPV recognizes as a detector needle ONLY
# because there is no exec sink on the line. The operand is abstracted to
# a placeholder as defense-in-depth (not a scanner requirement) so the
# shipped signature is not a copy-pasteable command.
DANGEROUS_ARG_SIGNATURES = (
    r"--insecure",
    r"--no-sandbox",
    r";\s*rm\s+-rf\s+<PATH>",
)
PRIVATE_IP_SIGNATURE = r"10\.\d+\.\d+\.\d+"
RM_RF_SIGNATURE = re.compile(r"rm\s+-rf\s+<PATH>")   # fed to re.search; operand abstracted
```

**WHY-INERT (flow-sensitive — the raw `r` prefix is NOT inert by
construction):** the raw-string proof holds ONLY when the literal is a
regex pattern fed to `re.<func>` or a rules-table element compared
against scanned content — there is **no call site** spreading it into a
shell or argv. `validate_security._match_inside_raw_string` +
`_DETECTOR_SIGNATURE_SKIP_RULES` skip RC-46 / RC-87 inside `r"..."`
**only when the match is not on an execution sink** (the skip is now
gated on the line carrying no `os.system` / `os.popen` /
`subprocess.getoutput` / `subprocess(shell=True)` / `eval` / `exec`
sink — Wave-1 flow-sensitive fix); skillaudit's flow-sensitive
`re`-pattern-literal verdict does the same. The string is data compared
against content, with no exec sink. The `<PATH>` placeholder is a
separate author best-practice (a signature should not be a
copy-pasteable command); it does **not** affect whether the scanner
skips the line — the scanner keys on the raw-string-in-a-data-context
shape, not on operand abstraction.

**VERIFY:** re-run security scan -> RC-46 / RC-87 / CMD_INJECTION on a
genuine DATA-only signature line gone (skipped as a detector needle, no
sink on the line), not demoted, not suppressed-by-config. A signature
line that ALSO carries an exec sink will (correctly) still fire — that
is not a devitalization, it is an evasion the scanner now rejects.

**SAFE when:** the strings genuinely ARE comparison needles fed to
`re.<func>` / a rules table (a scanner, an audit plugin, a linter) with
no sink on the line. **BREAKS / DO NOT USE when:** the literal is spread
onto an execution sink — `os.system("chromium " + r"--no-sandbox")` runs
**identically** to the plain string (a backslash-free flag is the same
bytes raw or plain: `r"--no-sandbox" == "--no-sandbox"`), so raw-stringing
a live exec argument is an **evasion, not a devitalization**, and the
scanner now fires on it regardless of the `r` prefix. (Raw-stringing also
changes the bytes when the literal contains metacharacters — `r"\s*"` is
not a real space — breaking an argv call.) If any sink consumes the
literal it is **load-bearing**: flag to the user, do not raw-string it.

---

## T2 — Doc `curl | bash` / install one-liner examples

Fires SUPPLY_CHAIN / CMD_INJECTION when a `.md` shows a remote fetch piped
straight into a shell.

**BEFORE** (described): a `bash`-fenced one-liner that fetches a remote
script over a real URL and pipes it directly into a shell — the
classic pipe-to-shell shape.

**AFTER** — choose by author intent:

- **(B1) defanged illustration** (the doc just *describes* the pattern,
  e.g. a security-plugin README explaining the threat). The `| bash`
  pipe-to-interpreter token MUST be removed — it is what the rules match;
  eliding only the URL is NOT enough:

  ````markdown
  A supply-chain attack looks like this (DO NOT RUN — illustration only):
  ```text
  curl <URL>   [pipes a remote script into a shell — DO NOT RUN]
  ```
  ````

  Or nominalize the whole line to prose with no literal `curl`/`| bash`
  token: *"…fetches a remote script and pipes it straight into a shell
  (description only)."* Fence language is `text` (not `bash`), and the
  `| <interpreter>` token is gone. (Re-scan confirmed: removing `| bash`
  clears it; merely switching the fence to `text` and eliding the URL
  while keeping `| bash` does NOT — both `skillaudit:supply_chain` and
  `skillaudit:code_execution` still fire, and in security mode cisco
  PIPELINE_TAINT_FLOW fires as a MAJOR.)

- **(B2) placeholder + split steps** (the doc is a *real* install guide
  and must stay actionable, but the pipe is the flagged shape):

  ````markdown
  1. Download the installer: `curl -fsSLo install.sh <RELEASE_URL>`
  2. **Review `install.sh`**, then run it: `bash install.sh`
  ````

  Breaking the pipe (download -> review -> run) is the security-correct
  install pattern anyway, and removes the piped-fetch token the rule
  matches.

**WHY-INERT:** the rules key on the literal token sequence `curl` + `|
<interpreter>`, independent of the fence language or whether the URL is
elided. (B1) once the `| bash` token is removed (or the line is
nominalized to prose), no `curl … | bash` adjacency remains on a single
fenced line — `skillaudit:supply_chain`, `skillaudit:code_execution`,
and cisco PIPELINE_TAINT_FLOW have nothing to match. (B2) the fetch and
the shell run are on separate lines, so no single line pipes a remote
fetch into a shell. A markdown file cannot execute either way — but the
rule keys on the token shape, so removing the shape clears it.

**VERIFY:** re-scan -> on a line where the `| <interpreter>` token has
been removed (B1) or the steps are split (B2): `skillaudit:supply_chain`
plus `skillaudit:code_execution` gone, and in security mode cisco
PIPELINE_TAINT_FLOW gone. If a cisco PIPELINE_TAINT_FLOW MAJOR (or a
demoted-to-NIT `skillaudit:supply_chain` in a `references/*.md` file)
still appears, the `curl` + `| bash` pair is still present on one line —
the defang is incomplete. B2 (split download → review → run) is the
reliably-clean path (re-scan verified LIVE = 0).

**SAFE when:** it is documentation (always — a `.md` never executes).
**BREAKS when:** the same one-liner also lives in an executable installer
(`.sh` / hook) — that copy is load-bearing and is NOT a doc; devitalizing
the doc is fine, but the executable copy is left and flagged (a genuine
security decision the author owns). **Irreversibility:** encoding,
compiling, or runtime-regenerating the removed pipe-to-shell token does
NOT satisfy the bar; the token must be absent from the shipped bytes.
**Caveat:** CPV already suppresses
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
entry point — same rigor as cpv-plugin-fixer-agent Guardrail 1). **BREAKS when:**
the sink is **live** — a plugin that legitimately shells out is
load-bearing; the real call cannot be devitalized without removing the
feature. If reachable, **flag to the user**: "this is a live shell-exec;
either harden it (argv-list + shell=False, validated input) or accept the
finding — the devitalizer will not silently break working behavior." The
job is to neutralize dead/example sinks, not delete features.
**Irreversibility:** encoding, compiling, or runtime-regenerating the
removed sink call does NOT satisfy the bar; the sink call must be absent
from the shipped bytes.

**FIRST, though — check it is a shell exec at all.** SHELL_EXEC's catalog
pattern for spawns is a bare `\bspawn\s*\(` with no receiver or language
guard, so it can match a construct that starts no process. The known case is
Rust IN-PROCESS concurrency — `std::thread::spawn`, `tokio::spawn`,
`rayon::spawn`, and `s.spawn(…)` inside `thread::scope` all start a thread or
async task in the SAME process: no shell, no `exec`, no child. The scanner
clears these since v5.1.0 (issue #188), so you should not normally be handed
one; if you are, **do NOT devitalize it** — there is nothing to neutralize,
and the flagged line is often a concurrency TEST whose second thread is the
whole proof, so removing it deletes the evidence rather than a threat. Report
it as a scanner false positive instead. `Command::new(...).spawn()` and
`Command::new("sh").arg("-c")` are the real thing and still fire.

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
**Irreversibility:** encoding, compiling, or runtime-regenerating the
removed exec/eval call does NOT satisfy the bar; the call must be absent
from the shipped bytes.

---

## T6 — Backtick command-substitution identifier in prose / docs

Fires CMD_INJECTION / SHELL_EXEC because a backtick-wrapped command reads
as command substitution — **but only when the inner command is not an
already-benign read-only recon command and no network sink is adjacent.**
Read-only recon backticks (`id`, `whoami`, `uname`, `hostname`, …) are
**already auto-certified benign** in markdown by skillaudit's
`_BENIGN_RECON_CMDS` discriminator and need NO transform — do not churn a
line that already passes (cross-cutting rule 1). T6 applies only when the
inner command is non-recon (e.g. a `curl`/fetch token) **or** a network
sink sits within ±3 lines (which forfeits the recon certification).

**BEFORE** (described): a sentence that wraps a non-recon command in
backticks — e.g. a `curl` to a remote URL — so it reads to the scanner as
a command-substitution shape that fires un-suppressed.

**AFTER** (form **D** — quote as prose, not as a code token):

```markdown
The hook captures remote config with the "curl" command pointed at a
remote URL.
```

Use straight or typographic quotes / the word "command" — anything that is
plainly an English mention, not a fenced / backtick shell token. Do NOT
simply nominalize a backtick that wraps a *dangerous literal* (e.g.
`` `cat /etc/passwd` `` or `` `nc -e /bin/sh …` ``): those tokens fire on
the literal substring even as prose, so quoting the words is not enough —
the literal itself must be removed/abstracted (T1/T4) or flagged.

**WHY-INERT:** a backtick-wrapped command reads to the scanner as a
command-substitution shape; an unquoted prose mention ("the curl command")
carries no backtick command-substitution token, so the CMD_INJECTION /
command-substitution match has nothing to key on. The sentence still
documents the behavior; nothing executes. (Re-scan verified: the backtick
`` `curl http://…` `` fires; the prose mention clears, LIVE = 0.)

**VERIFY:** re-scan -> the backtick command-substitution CMD_INJECTION
gone. First check the live report: if the inner command was a recon
command (`id`/`whoami`/…) with no adjacent sink, it was already
suppressed and never needed a transform.

**SAFE when:** it is documentation describing a non-recon command by name
(and the matched token was the backtick shape, not a dangerous literal).
**BREAKS when:** the backticks are *code* the reader is meant to copy-run
AND the surrounding fence is genuinely shell — but even then the markdown
does not execute; this transform only changes prose, so functionality is
unaffected. (The corresponding *executable* command in a hook is a
separate, possibly-load-bearing finding — T4 / T7 territory.)

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

## Irreversibility test

Before recording any transform as done, confirm it passes all three
questions. If any answer fails, the construct is hidden, not inert —
keep transforming (or flag).

1. **Is an execution-critical piece ABSENT from the shipped files?** The
   token a sink needs to run must be gone from the bytes the plugin
   ships — not encrypted, not compiled to a binary, not merely
   relocated to a demoting surface.
2. **Could a runtime path (decode / fetch / regenerate) restore it?** If
   a load-time base64 decode, a network fetch-then-assemble, or any code
   path could reconstruct the removed token at runtime → it is NOT
   inert.
3. **Is the remnant a comparison/scan needle only, with no call site?**
   What stays behind must be data compared against content (a
   raw-string signature with the operand abstracted), reachable by no
   execution sink.

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
