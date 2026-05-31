# Preflight Recipe

## Table of Contents

- [The Mechanical Preflight](#the-mechanical-preflight)
- [Step 1 — Validate Existing](#step-1--validate-existing)
- [Step 2 — Fetch Upstream and Cross-Check](#step-2--fetch-upstream-and-cross-check)
- [Step 3 — Emit](#step-3--emit)
- [Step 4 — Post-Emit Sanity Check](#step-4--post-emit-sanity-check)
- [When to Skip Steps](#when-to-skip-steps)
- [Failure Modes](#failure-modes)
- [Cross References](#cross-references)

## Checklist

- [ ] Step 1 (validate existing) — run on every modify / migrate flow
- [ ] Step 2 (fetch upstream + cross-check) — run on every create / modify / migrate flow
- [ ] Step 3 (emit) — only after step 2 passes
- [ ] Step 4 (post-emit sanity check) — MANDATORY on every flow, including pure creates

## The Mechanical Preflight

Every in-scope agent runs this 4-step recipe before declaring "done" on any marketplace.json work. Steps are deterministic — no LLM reasoning involved beyond running the commands and reading their output.

```bash
# 1. If editing existing marketplace.json
#    (upstream cross-validation runs UNCONDITIONALLY — there is no flag to toggle it).
PLUGIN_SKIP_GITHUB_INTEGRITY=1 uv run python scripts/validate_marketplace.py <mkpl-path> --strict

# 2. For every entry being added or modified:
for entry in <list>; do
    fetch_upstream_plugin_json <entry-source>
    assert entry.name == upstream.name
    assert entry.version is absent OR entry.version == upstream.version
done

# 3. Emit
write marketplace.json

# 4. Post-emit sanity check (same as step 1)
PLUGIN_SKIP_GITHUB_INTEGRITY=1 uv run python scripts/validate_marketplace.py <mkpl-path> --strict
```

Step 1 is OPTIONAL for fresh creates (there is nothing to validate yet). Step 4 is MANDATORY for every flow — pure creates, edits, migrations, fixes.

## Step 1 — Validate Existing

For flows that modify an existing marketplace.json, capture the baseline first:

```bash
PLUGIN_SKIP_GITHUB_INTEGRITY=1 uv run python scripts/validate_marketplace.py \
    <mkpl-path> --strict \
    > /tmp/preflight-baseline.txt
```

Reading the baseline answers two questions:

1. **What pitfalls already exist?** If the baseline has 3 MAJORs, the agent's edits must not introduce new MAJORs AND should ideally fix the existing ones (if in scope).
2. **What is the validator's view of the current state?** The agent must AGREE with the validator before drafting changes. If the validator says "entry foo-plugin has stale version", the agent must understand that finding before drafting an edit that touches the same entry.

For a fresh create (no existing marketplace.json), step 1 is skipped — there is nothing to baseline against. Proceed directly to step 2.

## Step 2 — Fetch Upstream and Cross-Check

For each plugin entry being added or modified, the agent:

```python
def preflight_entry(entry: dict, source_type: str) -> None:
    """Cross-check a marketplace entry against its upstream plugin.json."""
    upstream = fetch_upstream_plugin_json(entry, source_type)
    if upstream is None:
        # Network failed / repo private / etc.
        emit_draft_with_placeholder(entry)
        return

    # Name check
    assert entry["name"] == upstream["name"], (
        f"PIT-001 risk — drafted name {entry['name']!r} does not match "
        f"upstream {upstream['name']!r}. Refuse and ask user, OR copy "
        f"upstream verbatim."
    )

    # Version check
    REMOTE_SOURCES = {"github", "url", "git", "git-subdir", "npm"}
    if source_type in REMOTE_SOURCES:
        assert "version" not in entry, (
            f"PIT-002 risk — entry on remote source has version field. "
            f"Drop it; resolver uses upstream tag."
        )
    else:
        # local source — version REQUIRED and must match upstream
        assert entry.get("version") == upstream["version"], (
            f"local-source entry version {entry.get('version')!r} does "
            f"not match upstream {upstream['version']!r}."
        )

    # Field allowlist check (PIT-003, PIT-007)
    KNOWN_FIELDS = {
        "name", "description", "version", "author", "homepage", "repository",
        "license", "keywords", "source", "category", "tags", "claude_versions",
        "platforms", "alwaysLoad", "headersHelper", "repo", "url", "subdir",
        "ref", "package", "path"
    }
    unknown = set(entry.keys()) - KNOWN_FIELDS
    assert not unknown, f"PIT-003 risk — unknown fields: {unknown}"
```

This is the proactive half of the contract. If preflight fails, the agent DOES NOT emit — it refuses with the assertion message, OR (for unreachable upstream) emits a draft with placeholder and explicit comment.

## Step 3 — Emit

Once steps 1-2 pass, write the marketplace.json:

```python
mkpl_path.write_text(json.dumps(marketplace_data, indent=2) + "\n")
```

Use 2-space indent, trailing newline. JSON only — no comments (JSONC is for documentation, not on-disk marketplace.json). The pre-commit hooks in publish.py templates already enforce this.

If the file already has a `// FIXME` placeholder from step 2's unreachable-upstream branch, leave it — step 4 will flag the placeholder and force user resolution before the file is publishable.

## Step 4 — Post-Emit Sanity Check

The MANDATORY closing step. Re-run the validator against the just-written file:

```bash
PLUGIN_SKIP_GITHUB_INTEGRITY=1 uv run python scripts/validate_marketplace.py \
    <mkpl-path> --strict
```

Three possible outcomes:

| Validator exit | Meaning | Agent action |
|---|---|---|
| `0` — VALID | No findings, all checks pass | Declare done. |
| `1` — INVALID with new findings | The agent's edit introduced a regression | Re-enter step 2, fix the new finding, re-emit. Do NOT ship. |
| `1` — INVALID with same findings as baseline | The agent's edit did not address pre-existing issues, but did not regress | If the pre-existing finding is in scope for this flow, fix it now; otherwise log for the user. |

Step 4 is the safety net. TRDD-c0ee9543 Phase F adds a hook that REQUIRES step 4 before any agent declares "done" — but in practice, the agent should run it proactively without waiting for the hook.

## When to Skip Steps

- **Step 1** — skip for fresh creates (no baseline exists).
- **Step 2** — never skip. Even on pure renames or whitespace fixes, re-run cross-check.
- **Step 3** — only step that produces side effects. Never skip if the agent claims to "edit" the file.
- **Step 4** — never skip. The hook will block ship if missing.

The 2026-05-11 incident specifically tracked agents skipping step 4 because "the edit was small". Three of those edits were the ones that shipped name-mismatch bugs. Step 4 is non-negotiable.

## Failure Modes

| Failure | Resolution |
|---|---|
| Network failure during upstream fetch | Step 2 emits placeholder draft; step 4 flags the placeholder; ship blocked. |
| Validator finding the agent does not understand | Stop, ask user. Do NOT guess at a fix. |
| Validator finding is a CPV bug (false positive) | File a CPV issue; pin the agent's local CPV until fixed; document in the migration log. |
| Upstream plugin.json has its own validation errors | Out of scope — the marketplace contract assumes upstream is valid. Surface the upstream's issues to the user, do not fix from the marketplace side. |
| User insists on shipping anyway | Refuse. The contract is non-negotiable. The user can patch the file by hand if they want; the agent will not author broken marketplace.json. |

## Cross References

- [name-canonicalisation](name-canonicalisation.md) — preflight's name check
- [version-strategy](version-strategy.md) — preflight's version check
- [known-fields](known-fields.md) — preflight's field allowlist check
- [source-shape](source-shape.md) — preflight's per-source-type field check
- [common-pitfalls](common-pitfalls.md) — every preflight assertion corresponds to a PIT-NNN
