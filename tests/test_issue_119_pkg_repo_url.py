#!/usr/bin/env python3
"""Issue #119 — apt/dnf package-repository BASE URLs must not be flagged as dead.

`https://cli.github.com/packages` (and any apt/dnf repo base) is not a webpage:
it 404s by design because the package manager fetches `<base>/dists/…` /
`<base>/repodata/…`, never `<base>` itself. The link-checker was extracting the
bare URL token out of a standard apt `deb …` source line / dnf `baseurl=` /
`--add-repo` declaration and reporting `Dead URL (HTTP 404)`.

Two-sided coverage:
  * FP side — a URL on a recognised package-repo source line is skipped (no
    network request, no warning) across bare-prose, indented-code, and
    `baseurl=`/`--add-repo` forms.
  * Genuine side — a real dead markdown/prose link is STILL flagged, and the
    same repo-base URL appearing as a real markdown link (NOT on a repo line)
    is STILL checked — the skip is scoped to repo source lines, never the host.

`urllib.request.urlopen` is mocked so no real network is touched.
"""

from __future__ import annotations

import sys
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import patch

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_validation_common import (  # noqa: E402
    ValidationReport,
    _collect_package_repo_urls,
    validate_md_urls,
)

# The package-repo base under test and the sub-paths the package manager really
# fetches (those resolve fine; only the bare base 404s).
_PKG_BASE = "https://cli.github.com/packages"
_GENUINE_DEAD = "https://example.invalid/missing"


def _fake_urlopen(req, timeout=None, context=None):  # type: ignore[no-untyped-def]
    """Mock: the bare package base and the genuine-dead URL 404; all else 200."""
    url = req.full_url
    if url.rstrip("/") == _PKG_BASE or url.startswith(_PKG_BASE + "?"):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
    if "example.invalid" in url:
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    class _R:
        status = 200

        def __enter__(self) -> "_R":
            return self

        def __exit__(self, *a: object) -> None:
            return None

    return _R()


def _warnings(report: ValidationReport) -> list[str]:
    return [r.message for r in report.results if r.level == "WARNING"]


def _run(md_text: str) -> list[str]:
    """Write `md_text` to a temp markdown file, run the checker, return warnings."""
    tp = Path(tempfile.mkdtemp())
    md = tp / "install-recipes.md"
    md.write_text(md_text, encoding="utf-8")
    rep = ValidationReport()
    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        validate_md_urls(md, tp, rep, timeout=2.0)
    return _warnings(rep)


# ---------------------------------------------------------------------------
# _collect_package_repo_urls — the line-aware extractor
# ---------------------------------------------------------------------------


class TestCollectPackageRepoUrls:
    """Line-level detection of apt/dnf repo source declarations."""

    def test_apt_deb_line(self):
        """A `deb [opts] <base> suite component` line yields its base URL."""
        content = (
            "deb [arch=amd64 signed-by=/usr/share/keyrings/k.gpg] "
            f"{_PKG_BASE} stable main\n"
        )
        assert _collect_package_repo_urls(content) == {_PKG_BASE}

    def test_apt_deb_src_line(self):
        """`deb-src` source lines are recognised too."""
        content = f"deb-src [signed-by=/k.gpg] {_PKG_BASE} stable main\n"
        assert _collect_package_repo_urls(content) == {_PKG_BASE}

    def test_indented_deb_line(self):
        """A 4-space indented `deb` line (markdown code block) still matches."""
        content = f"Run:\n\n    deb [signed-by=/k.gpg] {_PKG_BASE} stable main\n"
        assert _collect_package_repo_urls(content) == {_PKG_BASE}

    def test_dnf_baseurl_line(self):
        """A dnf/yum `baseurl=<base>` line yields the base."""
        content = f"baseurl={_PKG_BASE}/rpm\nenabled=1\n"
        assert _collect_package_repo_urls(content) == {f"{_PKG_BASE}/rpm"}

    def test_dnf_add_repo_line(self):
        """A `dnf config-manager --add-repo <url>` line yields the url."""
        content = f"dnf config-manager --add-repo {_PKG_BASE}/rpm/gh-cli.repo\n"
        assert _collect_package_repo_urls(content) == {f"{_PKG_BASE}/rpm/gh-cli.repo"}

    def test_ordinary_prose_url_not_collected(self):
        """A plain prose / markdown-link URL is NOT a repo base — not collected."""
        content = f"See [docs]({_GENUINE_DEAD}) and visit {_PKG_BASE} maybe.\n"
        assert _collect_package_repo_urls(content) == set()


# ---------------------------------------------------------------------------
# validate_md_urls — FP side (repo-base URLs cleared)
# ---------------------------------------------------------------------------


class TestPackageRepoBaseNotFlagged:
    """The FP cases from issue #119 are now clean."""

    def test_bare_prose_deb_line_clean(self):
        """`deb … <base> stable main` in bare prose → no Dead-URL warning."""
        warns = _run(
            "Add the apt source:\n"
            "deb [arch=amd64 signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] "
            f"{_PKG_BASE} stable main\n"
        )
        assert warns == [], warns

    def test_indented_deb_line_clean(self):
        """A 4-space indented `deb` line → no Dead-URL warning."""
        warns = _run(f"Run:\n\n    deb [signed-by=/etc/k.gpg] {_PKG_BASE} stable main\n")
        assert warns == [], warns

    def test_dnf_baseurl_clean(self):
        """A dnf `baseurl=<base>` line → no Dead-URL warning."""
        warns = _run(f"baseurl={_PKG_BASE}/rpm\nenabled=1\ngpgcheck=1\n")
        assert warns == [], warns

    def test_dnf_add_repo_clean(self):
        """A `dnf config-manager --add-repo <url>` line → no Dead-URL warning."""
        warns = _run(f"dnf config-manager --add-repo {_PKG_BASE}/rpm/gh-cli.repo\n")
        assert warns == [], warns


# ---------------------------------------------------------------------------
# validate_md_urls — genuine side (real dead links still flagged)
# ---------------------------------------------------------------------------


class TestGenuineDeadLinksStillFlagged:
    """The skip is scoped to repo source lines — it does not silence real dead links."""

    def test_genuine_dead_markdown_link_flagged(self):
        """A plain dead markdown link still produces a Dead-URL warning."""
        warns = _run(f"See [the docs]({_GENUINE_DEAD}) for more.\n")
        assert any(_GENUINE_DEAD in w for w in warns), warns

    def test_mixed_repo_line_clean_and_dead_link_flagged(self):
        """A doc with BOTH a repo line and a genuine dead link: only the dead link flags."""
        warns = _run(
            f"deb [signed-by=/k.gpg] {_PKG_BASE} stable main\n\n"
            f"Also see [broken]({_GENUINE_DEAD}).\n"
        )
        assert any(_GENUINE_DEAD in w for w in warns), warns
        assert not any("cli.github.com/packages" in w for w in warns), warns

    def test_same_url_as_real_markdown_link_still_checked(self):
        """The SAME repo-base URL as a genuine markdown link (NOT on a deb line) is
        still checked and flagged — the skip never whitelists the host itself."""
        warns = _run(f"Visit [packages page]({_PKG_BASE}) directly.\n")
        assert any(_PKG_BASE in w for w in warns), warns
