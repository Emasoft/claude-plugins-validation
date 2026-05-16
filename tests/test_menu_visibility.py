#!/usr/bin/env python3
"""Regression tests for the v2.89.4 menu-visibility architecture
(TRDD-b8dd7f6b + TRDD-3ce2f864).

The v2.89.3 pattern called ``scripts/format_menu.py`` via Bash on every menu
turn. That generated the menu correctly — but Claude Code's UI only renders
the LLM's prose text output, NOT Bash tool stdout. End-user impact: typed
``/cpv-doctor``, saw "Ran 1 shell command", and the menu never appeared.

v2.89.4 pins these architectural rules:

1. **Static first-contact menus are pre-rendered as literal Unicode-box text
   in the slash-command body** — zero Bash, zero latency, always visible.
2. **Dynamic menus (auto-discovered rows) are now offloaded to the
   ``cpv-format-menu`` ``context: fork`` skill** (TRDD-3ce2f864). The
   orchestrator writes the JSON spec to ``/tmp/<cmd>-<purpose>-spec.json``
   and invokes the Skill tool. The fork-skill runs ``format_menu.py`` on a
   fresh haiku subagent (no inherited conversation history, so ``model:
   haiku`` actually takes effect) and returns the rendered text.
3. **The orchestrator body MUST carry an iron-clad "copy the Skill tool's
   text result / Bash stdout into your text" directive within 25 lines of
   every menu-render call** — both Bash stdout AND Skill tool results are
   invisible to the user, so without copying them the menu never appears.
4. **The orchestrators MUST NOT declare ``model: haiku`` in their
   frontmatter.** That was a lie in v2.89.0–v2.89.3: a multi-turn command
   body on opus with a 1M-token context cannot safely degrade mid-turn to
   haiku (per the Claude Code skills docs, the override applies "for the
   rest of the current turn" while keeping the inherited history, so the
   override silently degrades or fails). The honest version is to let the
   orchestrator run on the session model and fork the render step alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMANDS_DIR = REPO_ROOT / "commands"

# Four orchestrators with the menu-driven main-session pattern.
ORCHESTRATORS = [
    "cpv-doctor.md",
    "cpv-fix-validation.md",
    "cpv-fix-marketplace-validation.md",
    "cpv-cache-optimize.md",
]


def _read(cmd: str) -> str:
    return (COMMANDS_DIR / cmd).read_text(encoding="utf-8")


def test_cpv_doctor_first_menu_is_pre_rendered() -> None:
    """`/cpv-doctor` must embed its first-contact menu as literal Unicode-box
    text in the body (NOT call format_menu.py for it). The body must contain
    the rendered table including the "Diagnose what?" header row and at least
    one numbered data row.

    This is the v2.89.4 fix: pre-rendering eliminates the ~27s Bash round-trip
    AND makes the menu visible to the user (Bash stdout is invisible in the UI).
    """
    body = _read("cpv-doctor.md")
    # Look for the heavy top fence and the header row that the pre-rendered
    # menu must contain.
    assert "┏━" in body, (
        "cpv-doctor.md must embed the first-contact menu as literal "
        "Unicode-box text (heavy top fence ┏━ missing). The first menu is "
        "static — pre-render it once and embed verbatim instead of calling "
        "format_menu.py on every invocation."
    )
    assert "┃  # ┃ Diagnose what?" in body, (
        "cpv-doctor.md must embed the pre-rendered first-contact menu with "
        "the literal header row '┃  # ┃ Diagnose what?'. The first menu must "
        "be pre-rendered to avoid the Bash round-trip + tool-stdout-invisible "
        "bug from v2.89.3."
    )
    # Sanity: at least one numbered row of the embedded menu is present.
    assert "│  1 │" in body, (
        "cpv-doctor.md must embed the rendered numbered rows of the "
        "first-contact menu (e.g. '│  1 │' for the 'specific plugin' row)."
    )


def test_cpv_doctor_first_menu_instructs_verbatim_copy() -> None:
    """The cpv-doctor body must instruct the orchestrator to copy the embedded
    first menu verbatim into its text response (no tool call, no paraphrase)."""
    body = _read("cpv-doctor.md")
    assert "VERBATIM" in body, (
        "cpv-doctor.md must contain the directive 'VERBATIM' instructing "
        "the orchestrator to copy the embedded first menu verbatim into its "
        "text response."
    )


@pytest.mark.parametrize("cmd", ORCHESTRATORS)
def test_orchestrator_has_copy_stdout_directive(cmd: str) -> None:
    """Every orchestrator command body that invokes the menu renderer — either
    via Bash ``format_menu.py`` (legacy path) OR via the ``cpv-format-menu``
    Skill tool (v2.89.4 path) — MUST carry an iron-clad "copy the result into
    your text response" directive within 25 lines of the call. Otherwise the
    menu is invisible to the user: Claude Code's UI only renders LLM text
    output, not Bash stdout AND not Skill tool results.

    Required tokens (case-insensitive) in the window:
      - One verb of {COPY, ECHO}
      - One render-output ref of {STDOUT, BASH OUTPUT, SKILL OUTPUT, SKILL RESULT}
      - One emphasis of {VERBATIM, INVISIBLE}
    """
    body = _read(cmd)
    lines = body.splitlines()
    # Find every line that invokes a menu renderer — either the Bash path
    # (``format_menu.py menu``) or the Skill path. The Skill path uses the
    # fully-qualified ``skill: "claude-plugins-validation:cpv-format-menu"``
    # form inside a Skill({...}) invocation. We match on that exact string
    # so prose mentions of "fork-skill:" or "this skill" don't false-match.
    fm_call_lines = []
    for i, ln in enumerate(lines):
        if "format_menu.py" in ln and ".py menu" in ln:
            fm_call_lines.append(i)
        elif 'skill: "claude-plugins-validation:cpv-format-menu"' in ln:
            fm_call_lines.append(i)
    if not fm_call_lines:
        pytest.skip(
            f"{cmd} has no menu-render call (neither format_menu.py via "
            f"Bash nor cpv-format-menu via Skill). Probably pre-rendered "
            f"everything."
        )
    # Within +/- 25 lines of each call, there must be a copy-result directive.
    for call_line in fm_call_lines:
        window_lo = max(0, call_line - 25)
        window_hi = min(len(lines), call_line + 25)
        window = "\n".join(lines[window_lo:window_hi]).upper()
        has_copy_verb = any(verb in window for verb in ("COPY", "ECHO"))
        has_output_ref = (
            "STDOUT" in window
            or "BASH OUTPUT" in window
            or "SKILL OUTPUT" in window
            or "SKILL RESULT" in window
            or "SKILL TOOL'S TEXT RESULT" in window
        )
        has_emphasis = ("VERBATIM" in window) or ("INVISIBLE" in window)
        assert has_copy_verb and has_output_ref and has_emphasis, (
            f"{cmd} has a menu-render call near line {call_line + 1} "
            f"without an iron-clad copy-result directive within 25 lines. "
            f"Required tokens (case-insensitive): one of {{COPY, ECHO}}; one "
            f"of {{STDOUT, BASH OUTPUT, SKILL OUTPUT, SKILL RESULT}}; one "
            f"of {{VERBATIM, INVISIBLE}}. Both Bash stdout AND Skill tool "
            f"results are invisible to the user — without this directive "
            f"the menu never appears in the UI (regression of v2.89.3 bug)."
        )


@pytest.mark.parametrize("cmd", ORCHESTRATORS)
def test_orchestrators_have_no_lying_model_haiku(cmd: str) -> None:
    """The four orchestrators MUST NOT declare ``model: haiku`` in their
    frontmatter (TRDD-3ce2f864).

    Per the Claude Code skills docs, ``model:`` overrides apply "for the rest
    of the current turn" while keeping the inherited conversation history.
    The orchestrators are multi-turn state machines that run in the main
    session — typically on opus with a 1M-token context — so the override
    silently degrades or fails. The honest pattern is: orchestrator runs on
    the session model; menu rendering forks to haiku via the
    ``cpv-format-menu`` ``context: fork`` skill.
    """
    body = _read(cmd)
    # Extract just the frontmatter block (between the leading "---" and the
    # next "---" on its own line). We must only inspect the frontmatter — the
    # body explicitly mentions ``model: haiku`` in the architecture-notes
    # section as part of the honest explanation of WHY the field was removed.
    assert body.startswith("---"), f"{cmd} missing frontmatter — first line is not '---'"
    parts = body.split("\n---\n", 1)
    if len(parts) != 2:
        parts = body.split("\n---", 1)
    head = parts[0]
    frontmatter = head.split("\n", 1)[1] if "\n" in head else ""
    assert "model: haiku" not in frontmatter, (
        f"{cmd} frontmatter declares `model: haiku`. Per TRDD-3ce2f864 the "
        f"four orchestrator commands MUST NOT carry that field — it was a "
        f"lie (multi-turn opus body cannot safely degrade mid-turn to haiku). "
        f"Remove the field and rely on the `cpv-format-menu` `context: fork` "
        f"skill for honest haiku menu rendering."
    )


@pytest.mark.parametrize("cmd", ORCHESTRATORS)
def test_orchestrators_invoke_cpv_format_menu_skill(cmd: str) -> None:
    """The four orchestrators MUST reference the ``cpv-format-menu`` skill at
    least once in the body (TRDD-3ce2f864) — proof they offload menu
    rendering to the fork-skill instead of calling ``format_menu.py`` directly
    via Bash on the inherited (often opus-sized) main-session context.
    """
    body = _read(cmd)
    assert "cpv-format-menu" in body, (
        f"{cmd} body does not reference `cpv-format-menu`. Per TRDD-3ce2f864 "
        f"the four orchestrator commands MUST offload menu rendering to the "
        f"`cpv-format-menu` `context: fork` skill (invoked via the Skill "
        f"tool). Direct `format_menu.py` Bash calls in the orchestrator turn "
        f"are the legacy path that was deprecated in v2.89.4."
    )


@pytest.mark.parametrize("cmd", ORCHESTRATORS)
def test_orchestrator_has_no_haiku_lie_banner(cmd: str) -> None:
    """No orchestrator may claim "Menu rendering is currently haiku" — the
    model:haiku frontmatter is best-effort and frequently ignored. Banners
    must be honest tips, not lies about the active model.

    The body must mention `/model haiku` (per existing
    test_orchestrator_command_documents_haiku_banner) — but it must NOT
    claim the menu is "currently haiku" or "running on haiku" as that
    assertion is unverifiable from the slash-command body.
    """
    body = _read(cmd).lower()
    forbidden_phrases = [
        "currently haiku",
        "running on haiku",
        "menu rendering is currently haiku",
    ]
    for phrase in forbidden_phrases:
        assert phrase not in body, (
            f"{cmd} contains the dishonest phrase '{phrase}'. The model:haiku "
            f"frontmatter is best-effort and frequently ignored; the banner "
            f"must suggest /model haiku as a user opt-in tip, not claim the "
            f"current turn is already running on haiku."
        )
