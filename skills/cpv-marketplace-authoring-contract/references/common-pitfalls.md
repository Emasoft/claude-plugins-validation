# Common Pitfalls

## Table of Contents

- [PIT-001 — Name Mismatch via Suffix Stripping](#pit-001--name-mismatch-via-suffix-stripping)
- [PIT-002 — Stale Version on Remote Source](#pit-002--stale-version-on-remote-source)
- [PIT-003 — Top-Level Scope Field](#pit-003--top-level-scope-field)
- [PIT-004 — Layout C Self-Entry Missing Source](#pit-004--layout-c-self-entry-missing-source)
- [PIT-005 — Source GitHub With Full URL](#pit-005--source-github-with-full-url)
- [PIT-006 — Homepage Pointing at Wrong Repo](#pit-006--homepage-pointing-at-wrong-repo)
- [PIT-007 — Category With Arbitrary Value](#pit-007--category-with-arbitrary-value)
- [Cross References](#cross-references)

## Checklist

- [ ] Run `validate_marketplace.py --strict` against the draft entry — upstream cross-validation runs unconditionally (no flag), and every pitfall has a finding code
- [ ] If the validator reports a pitfall, apply the auto-fix recipe in that pitfall's section below
- [ ] If the recipe does not apply, fix manually and re-validate

## PIT-001 — Name Mismatch via Suffix Stripping

**Incident date:** 2026-05-11.

**Pattern:** Marketplace entry name is shorter than the upstream `plugin.json.name`, typically because the agent stripped a `-plugin` / `-cli` / `-helper` suffix it considered "redundant".

**Detection regex (for the fixer):**
```python
upstream_name = fetch_upstream("plugin.json")["name"]
if entry["name"] != upstream_name:
    raise NameMismatch(entry["name"], upstream_name)
```

**Auto-fix recipe:**
```python
entry["name"] = upstream_name    # restore the canonical name byte-for-byte
```

**Example before:**
```json
{"name": "visual-communicator", "source": "github", "repo": "owner/ai-maestro-visual-communicator-plugin"}
```

**Example after:**
```json
{"name": "ai-maestro-visual-communicator-plugin", "source": "github", "repo": "owner/ai-maestro-visual-communicator-plugin"}
```

**Why agents do it:** "It looks cleaner." Forbidden. See [name-canonicalisation](name-canonicalisation.md).

**Validator finding:** `MAJOR — marketplace entry "X" name differs from upstream plugin.json name "Y"`.

## PIT-002 — Stale Version on Remote Source

**Incident date:** 2026-05-11.

**Pattern:** Marketplace entry on a remote source (`github`, `url`, `git`, `git-subdir`, `npm`) declares a `version` field. The field goes stale within hours of every upstream release.

**Detection regex (for the fixer):**
```python
REMOTE_SOURCES = {"github", "url", "git", "git-subdir", "npm"}
if entry["source"] in REMOTE_SOURCES and "version" in entry:
    raise StaleVersionRisk(entry["name"], entry["version"])
```

**Auto-fix recipe:**
```python
if entry["source"] in REMOTE_SOURCES:
    del entry["version"]    # DROP — install resolver consults upstream tag
```

**Example before:**
```json
{
  "name": "foo-plugin",
  "version": "1.0.0",
  "source": "github",
  "repo": "owner/foo-plugin"
}
```

**Example after:**
```json
{
  "name": "foo-plugin",
  "source": "github",
  "repo": "owner/foo-plugin"
}
```

**Why agents do it:** "Versions look important." They are not, for remote sources — the install resolver consults the upstream git tag, not the marketplace's field. See [version-strategy](version-strategy.md).

**Validator finding:** `MAJOR — marketplace entry "X" has version field on remote source — drop it (resolver uses upstream tag)`.

## PIT-003 — Top-Level Scope Field

**Incident date:** 2026-05-11.

**Pattern:** Marketplace entry includes a top-level `scope` field — typically `"scope": "user"` or `"scope": "project"` — conflated with the `claude plugin install --scope <X>` install flag.

**Detection regex (for the fixer):**
```python
if "scope" in entry:
    raise UnknownField(entry["name"], "scope")
```

**Auto-fix recipe:**
```python
del entry["scope"]    # field is not in the allowlist
# Recommendation: document scope in plugin README, OR set as a default in plugin.json's settings block.
```

**Example before:**
```json
{
  "name": "foo-plugin",
  "scope": "user",
  "source": "github",
  "repo": "owner/foo-plugin"
}
```

**Example after:**
```json
{
  "name": "foo-plugin",
  "source": "github",
  "repo": "owner/foo-plugin"
}
```

**Why agents do it:** Confusion between marketplace entry fields and install-time CLI flags. The marketplace describes WHAT the plugin is, not WHERE the user wants it installed. See [known-fields](known-fields.md).

**Validator finding:** `MAJOR — marketplace entry "X" has unknown field: scope`.

## PIT-004 — Layout C Self-Entry Missing Source

**Incident date:** v2.32.0 (during Layout C migration validator development).

**Pattern:** Layout C self-entry omits the `"source": "./"` literal — the agent assumes the resolver will infer Layout C from "marketplace.json sits next to plugin.json". It does not.

**Detection regex (for the fixer):**
```python
# Detect Layout C by file colocation
is_layout_c = (mkpl_path.parent / "plugin.json").is_file()
if is_layout_c and entry.get("source") != "./":
    raise LayoutCMissingSource(entry["name"])
```

**Auto-fix recipe:**
```python
entry["source"] = "./"    # literal two-char string, not "."
```

**Example before:**
```json
{
  "name": "foo-plugin",
  "version": "1.0.0",
  "description": "Foo plugin"
}
```

**Example after:**
```json
{
  "name": "foo-plugin",
  "version": "1.0.0",
  "source": "./",
  "description": "Foo plugin"
}
```

**Why agents do it:** "Inferable from context." Resolver does not infer. The literal `"./"` is the explicit Layout C marker. See [source-shape](source-shape.md).

**Validator finding:** `MAJOR — Layout C self-entry "X" missing source — add "source": "./"`.

## PIT-005 — Source GitHub With Full URL

**Incident date:** v2.22.x.

**Pattern:** `source: github` entry uses a full URL in `repo` instead of the `owner/repo` shorthand.

**Detection regex (for the fixer):**
```python
if entry["source"] == "github" and entry["repo"].startswith("http"):
    raise GitHubRepoNotShorthand(entry["name"], entry["repo"])
```

**Auto-fix recipe:**
```python
import re
match = re.match(r"https://github\.com/([^/]+)/([^/]+?)(\.git)?/?$", entry["repo"])
if match:
    entry["repo"] = f"{match.group(1)}/{match.group(2)}"
```

**Example before:**
```json
{
  "name": "foo-plugin",
  "source": "github",
  "repo": "https://github.com/owner/foo-plugin.git"
}
```

**Example after:**
```json
{
  "name": "foo-plugin",
  "source": "github",
  "repo": "owner/foo-plugin"
}
```

**Why agents do it:** Copy-paste from URL bar. Forbidden because the `github` source expects shorthand; full URLs use `source: url` instead. See [source-shape](source-shape.md).

**Validator finding:** `MAJOR — marketplace entry "X" with source=github must use "owner/repo" shorthand, got URL`.

## PIT-006 — Homepage Pointing at Wrong Repo

**Incident date:** legacy.

**Pattern:** `homepage` field points at a repo URL that differs from `source.repo` / `source.url`, suggesting copy-paste from a sibling entry.

**Detection regex (for the fixer):**
```python
homepage_repo = extract_github_repo(entry.get("homepage", ""))
source_repo = entry.get("repo") or extract_github_repo(entry.get("url", ""))
if homepage_repo and source_repo and homepage_repo != source_repo:
    raise HomepageRepoMismatch(entry["name"], homepage_repo, source_repo)
```

**Auto-fix recipe:** REQUIRES USER REVIEW. The fixer flags the entry; the user must decide whether the homepage or the source is wrong. Auto-fix is NOT safe — it can clobber an intentionally-different docs site.

**Example before:**
```json
{
  "name": "foo-plugin",
  "homepage": "https://github.com/owner/bar-plugin",
  "source": "github",
  "repo": "owner/foo-plugin"
}
```

**Why agents do it:** Copy-paste from another entry. The `homepage` and `source` should normally agree (or `homepage` should be a docs site URL like `https://foo-plugin.example.com`, not a different repo).

**Validator finding:** `MINOR — marketplace entry "X" homepage and source point at different repos — verify intentional`.

## PIT-007 — Category With Arbitrary Value

**Incident date:** v2.x.

**Pattern:** `category` field set to a free-form user string ("My personal tools", "AI stuff", "etc.") instead of a value from the canonical taxonomy.

**Detection regex (for the fixer):**
```python
CANONICAL_CATEGORIES = {
    "ai", "automation", "code-quality", "data", "devops", "documentation",
    "education", "git", "infrastructure", "monitoring", "productivity",
    "security", "testing", "utilities", "web",
}
if "category" in entry and entry["category"] not in CANONICAL_CATEGORIES:
    raise NonCanonicalCategory(entry["name"], entry["category"])
```

**Auto-fix recipe:** REQUIRES USER REVIEW. The fixer offers the closest matches from the canonical list via `AskUserQuestion`.

**Example before:**
```json
{
  "name": "foo-plugin",
  "category": "My personal coding tools",
  "source": "github",
  "repo": "owner/foo-plugin"
}
```

**Example after:**
```json
{
  "name": "foo-plugin",
  "category": "productivity",
  "tags": ["coding", "personal"],
  "source": "github",
  "repo": "owner/foo-plugin"
}
```

**Why agents do it:** No exposure to the canonical taxonomy. Use `tags` for fine-grained labels — `category` is for broad bucketing only. See [known-fields](known-fields.md).

**Validator finding:** `MINOR — marketplace entry "X" category "Y" not in canonical taxonomy`.

## Cross References

- [name-canonicalisation](name-canonicalisation.md) — PIT-001's root cause documentation
- [version-strategy](version-strategy.md) — PIT-002's root cause documentation
- [known-fields](known-fields.md) — PIT-003 and PIT-007's root cause documentation
- [source-shape](source-shape.md) — PIT-004 and PIT-005's root cause documentation
- [preflight-recipe](preflight-recipe.md) — the recipe that catches every PIT-NNN before emit
