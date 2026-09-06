"""Regression guards for the batch-24 audit fixes (doc-reference correctness).

Every finding in this batch was a broken/stale reference in a .md surface:

  * #29  agents/cpv-plugin-creator-agent.md — completion-gate fix loop carried a
          hardcoded "hard cap 5 iterations", violating the no-hardcoded-
          iteration-caps rule (only empty-set / oscillation may terminate).
  * #21  commands/cpv-batch-caching-optimize.md — referenced a ghost
          slash command `/cpv-cache-optimize` (4x) that does not exist; the
          real interactive Phase-4 entry point is `/cpv-main-menu`.
  * #166 commands/cpv-batch-fix.md — Step 0 ran the orchestrator `plan`
          (writing plan.json to /tmp) and then the marketplace path re-ran
          `plan`, abandoning the first; the fan-out now reuses Step 0's plan.
  * #24  skills/cpv-scaffold-skill/SKILL.md — the example `--description`
          lacked the "Use when ..." trigger the skill itself mandates.
  * #110 skills/cpv-scaffold-skill/SKILL.md — the Output section overclaimed 5
          frontmatter fields; add_component._skill_template emits only 2.
  * HIGH + #113 skills/cpv-setup-github-marketplace/references/
          marketplace-setup-guide.md — referenced ghost scripts
          `update_marketplace_metadata.py` and `setup_git_hooks.py` (plus a
          bogus `--marketplace-dir` flag); the real templates are
          `generate-readme.py` and `setup-hooks.py`.

These tests pin the corrected state and would have failed against the
pre-fix files, so they double as guards that would have caught the bugs.
They are pure text assertions over the repo's .md surfaces plus one
structural assertion over the real `_skill_template` frontmatter.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PLUGIN_CREATOR = REPO_ROOT / "agents" / "cpv-plugin-creator-agent.md"
BATCH_CACHE_OPT = REPO_ROOT / "commands" / "cpv-batch-caching-optimize.md"
BATCH_FIX = REPO_ROOT / "commands" / "cpv-batch-fix.md"
SCAFFOLD_SKILL = REPO_ROOT / "skills" / "cpv-scaffold-skill" / "SKILL.md"
MKPL_GUIDE = REPO_ROOT / "skills" / "cpv-setup-github-marketplace" / "references" / "marketplace-setup-guide.md"
SCRIPT_TEMPLATES = REPO_ROOT / "skills" / "cpv-setup-github-marketplace" / "references" / "script-templates.md"
ADD_COMPONENT = REPO_ROOT / "scripts" / "add_component.py"


# ---- #29: no hardcoded iteration cap in cpv-plugin-creator-agent completion gate ----


def test_plugin_creator_has_no_hardcoded_iteration_cap() -> None:
    """cpv-plugin-creator-agent.md must not carry a 'hard cap N iterations' fix-loop ceiling."""
    text = PLUGIN_CREATOR.read_text(encoding="utf-8")
    # The exact pre-fix wording and any equivalent magic-number ceiling.
    assert "hard cap 5 iterations" not in text
    assert not re.search(r"hard cap\s+\d+\s+iter", text, re.IGNORECASE)
    assert not re.search(r"\b(max|cap|capped at)\s+\d+\s+iter", text, re.IGNORECASE)


def test_plugin_creator_uses_oscillation_only_termination() -> None:
    """The completion gate must terminate only on empty-set or oscillation."""
    text = PLUGIN_CREATOR.read_text(encoding="utf-8")
    assert "NO hardcoded iteration cap" in text
    assert "oscillat" in text.lower()


# ---- #21: /cpv-cache-optimize is a ghost; real entry is /cpv-main-menu ----


def test_batch_cache_optimize_has_no_ghost_command() -> None:
    """The batch cache-optimize command must not reference the non-existent /cpv-cache-optimize."""
    text = BATCH_CACHE_OPT.read_text(encoding="utf-8")
    # Match the ghost SLASH-COMMAND only, boundary-guarded: after the v3.0.0
    # rename the legit agent `cpv-cache-optimizer-agent` contains the substring
    # `cpv-cache-optimize`, so a bare substring check would false-fire on it.
    assert not re.search(r"/cpv-cache-optimize(?![\w-])", text)


def test_batch_cache_optimize_points_at_real_entry_point() -> None:
    """Phase-4 redirect must name the real /cpv-main-menu interactive flow."""
    text = BATCH_CACHE_OPT.read_text(encoding="utf-8")
    assert "/cpv-main-menu" in text
    # And the command it claims to redirect to must actually exist on disk.
    assert (REPO_ROOT / "commands" / "cpv-main-menu.md").is_file()
    # The ghost target must NOT exist as a command file.
    assert not (REPO_ROOT / "commands" / "cpv-cache-optimize.md").exists()


# ---- #166: cpv-batch-fix reuses Step 0 plan; only ONE orchestrator plan call ----


def test_batch_fix_invokes_orchestrator_plan_exactly_once() -> None:
    """Only Step 0 may run `cpv_batch_orchestrator.py plan`; the fan-out reuses it.

    Count only the *executable* shell form (the quoted ``…/scripts/...py" plan``
    invocation), not the inline-backtick prose reference in Step M1 that explains
    Step 0 already ran it.
    """
    text = BATCH_FIX.read_text(encoding="utf-8")
    invocations = re.findall(r'scripts/cpv_batch_orchestrator\.py"\s+plan\b', text)
    assert len(invocations) == 1, f"expected 1 orchestrator plan invocation, found {len(invocations)}"


def test_batch_fix_marketplace_path_reuses_step0_plan() -> None:
    """Step M1 must reuse SESSION_DIR/plan.json rather than re-planning."""
    text = BATCH_FIX.read_text(encoding="utf-8")
    assert "Reuse the per-plugin plan from Step 0" in text
    assert "Do NOT re-plan" in text
    # Step 0 must capture the session dir + status table for reuse.
    assert "SESSION_DIR=$(echo" in text
    assert "STATUS_TABLE=$(echo" in text


# ---- #24 + #110: cpv-scaffold-skill example + Output match reality ----


def test_scaffold_skill_example_includes_use_when_trigger() -> None:
    """The example --description must carry the 'Use when' trigger the skill mandates."""
    text = SCAFFOLD_SKILL.read_text(encoding="utf-8")
    # Isolate the fenced Examples bash block.
    m = re.search(r"## Examples\s+```bash\n(.*?)```", text, re.DOTALL)
    assert m is not None, "Examples bash block not found"
    example = m.group(1)
    assert "--description" in example
    assert "Use when" in example
    # The bare pre-fix example must be gone.
    assert '--description "What it does"' not in example


def test_scaffold_skill_output_matches_real_template_fields() -> None:
    """Output section must claim exactly the fields _skill_template emits (name + description)."""
    text = SCAFFOLD_SKILL.read_text(encoding="utf-8")
    # The false pre-fix sentence claiming a 5-field canonical frontmatter must be gone.
    assert (
        "Frontmatter follows canonical Claude Code spec: `name`, `description`, "
        "`when_to_use`, `user-invocable`, `allowed-tools`"
    ) not in text
    assert "Frontmatter emits only `name` and `description`" in text


def test_skill_template_really_emits_only_name_and_description() -> None:
    """Guard the ground truth: add_component._skill_template frontmatter has 2 keys."""
    src = ADD_COMPONENT.read_text(encoding="utf-8")
    m = re.search(r"def _skill_template\(.*?\)\s*->\s*str:\s*\n\s*return f?\"\"\"(.*?)\"\"\"", src, re.DOTALL)
    assert m is not None, "_skill_template body not found"
    body = m.group(1)
    # Pull the leading YAML frontmatter block of the template.
    fm = re.search(r"^---\n(.*?)\n---", body, re.DOTALL)
    assert fm is not None, "template has no frontmatter block"
    keys = re.findall(r"^([A-Za-z][A-Za-z0-9_-]*):", fm.group(1), re.MULTILINE)
    assert keys == ["name", "description"], f"template frontmatter keys drifted: {keys}"
    # The over-claimed fields are genuinely absent from the emitted stub.
    for ghost in ("when_to_use:", "user-invocable:", "allowed-tools:"):
        assert ghost not in fm.group(1)


# ---- HIGH + #113: marketplace-setup-guide references real templates only ----


def test_marketplace_guide_has_no_ghost_script_names() -> None:
    """The guide must not reference update_marketplace_metadata.py or setup_git_hooks.py."""
    text = MKPL_GUIDE.read_text(encoding="utf-8")
    assert "update_marketplace_metadata" not in text
    assert "setup_git_hooks" not in text
    # The bogus flag (neither real template accepts it) must be gone too.
    assert "--marketplace-dir" not in text


def test_marketplace_guide_references_real_templates() -> None:
    """Every script the guide names for README/hooks must be defined in script-templates.md.

    The README generator is `render_readme_table.py`. It replaced a doc-only
    `generate-readme.py` that script-templates.md described but
    setup_marketplace_automation.py never copied — so the old name this test used to
    pin was, despite this test's own name, never a real template. The replacement IS
    shipped, which is why the assertion below now also checks it exists on disk.
    """
    text = MKPL_GUIDE.read_text(encoding="utf-8")
    assert "render_readme_table.py" in text
    assert "setup-hooks.py" in text
    templates = SCRIPT_TEMPLATES.read_text(encoding="utf-8")
    assert "## render_readme_table.py" in templates
    assert "## setup-hooks.py" in templates
    assert "## sync_marketplace_versions.py" in templates
    # Scoped to the section HEADING, not a bare substring: the invariant is that the
    # retired generator is never again DOCUMENTED AS A TEMPLATE, and a blanket
    # `"generate-readme.py" not in text` would also reject a legitimate migration note
    # telling a reader what the old name became.
    assert "## generate-readme.py" not in templates, "the superseded doc-only generator must not return"
    shipped = REPO_ROOT / "templates" / "scripts" / "render_readme_table.py"
    assert shipped.is_file(), f"documented generator is not shipped at {shipped}"
