#!/usr/bin/env python3
"""
Issue #188 — skillaudit SHELL_EXEC false-positive on Rust in-process spawns.

The catalog's SHELL_EXEC pattern index 2 is a bare `\\bspawn\\s*\\(` aimed at
`subprocess.Popen` / `child_process.spawn` / `Command::spawn`. With no receiver
or language guard it also matches `std::thread::spawn(`, which starts an OS
THREAD inside the same process: no shell, no `exec`, no child process.

Why it had to be fixed rather than worked around: the finding lands at `medium`
-> MINOR, and a MINOR blocks `--strict` (exit 3). So one false positive gated a
release whose CRITICAL and MAJOR counts were both 0 — and the reporter could not
edit the flagged line away, because it is a `#[cfg(test)]` proof that a write
lock serializes two concurrent holders and therefore REQUIRES a second thread.

Every test here is two-sided. The FP-clears half alone would also pass against a
discriminator that cleared every `spawn(` in Rust, which would be a security
false negative — so the must-still-fire half is the load-bearing one.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

scripts_dir = Path(__file__).parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from cpv_skillaudit_native import scan_content  # noqa: E402


def _shell_exec_fires(rust_source: str) -> bool:
    """Run the REAL scanner over a .rs file and report whether SHELL_EXEC fires.

    Goes through `scan_content` rather than the classifier directly, so the test
    exercises the dispatch path a real scan takes.
    """
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "write_gate.rs"
        p.write_text(rust_source, encoding="utf-8")
        findings = [
            f
            for f in scan_content(rust_source, str(p))
            if not f.get("suppressed") and f.get("ruleId") == "SHELL_EXEC"
        ]
    return bool(findings)


# ---------------------------------------------------------------------------
# The FP half — in-process concurrency must NOT fire
# ---------------------------------------------------------------------------
class TestInProcessSpawnsDoNotFire:
    """A thread / async-task spawn runs in THIS process — not a shell."""

    def test_issue_188_verbatim_reporter_case(self) -> None:
        """The exact shape from issue #188 (a cfg(test) concurrency proof)."""
        src = (
            "#[cfg(test)]\n"
            "mod tests {\n"
            "    #[test]\n"
            "    fn lock_serializes_two_holders() {\n"
            "        let handle = std::thread::spawn(move || {\n"
            '            let _g2 = acquire(&scope2).expect("second acquire must succeed");\n'
            '            order2.lock().unwrap().push("second");\n'
            "        });\n"
            "        handle.join().unwrap();\n"
            "    }\n"
            "}\n"
        )
        assert not _shell_exec_fires(src)

    def test_imported_thread_spawn(self) -> None:
        """`thread::spawn` (via `use std::thread`) is the same construct."""
        assert not _shell_exec_fires("let h = thread::spawn(|| do_work());")

    def test_tokio_spawn(self) -> None:
        """`tokio::spawn` schedules an async task on the runtime."""
        assert not _shell_exec_fires("tokio::spawn(async move { serve().await; });")

    def test_tokio_spawn_blocking(self) -> None:
        """`tokio::task::spawn_blocking` uses the blocking pool, still in-process.

        NOTE — this one is a CONTROL, not evidence for the fix. Verified against
        the unfixed classifier: it passed there too, because the catalog pattern
        is `\\bspawn\\s*\\(` and `spawn_blocking(` puts `_blocking` between the
        token and the paren, so it never matched. Kept so a future widening of
        that pattern cannot silently start flagging it; do not cite it as proof
        that this discriminator works.
        """
        assert not _shell_exec_fires("tokio::task::spawn_blocking(move || heavy());")

    def test_rayon_spawn(self) -> None:
        """`rayon::spawn` hands work to the rayon thread pool."""
        assert not _shell_exec_fires("rayon::spawn(|| compute());")

    def test_async_std_task_spawn(self) -> None:
        """`async_std::task::spawn` is the async-std equivalent."""
        assert not _shell_exec_fires("async_std::task::spawn(async { go().await });")

    def test_scoped_spawn_inside_thread_scope(self) -> None:
        """`s.spawn(...)` inside `std::thread::scope(|s| …)`."""
        src = "std::thread::scope(|s| {\n    s.spawn(|| worker_one());\n});\n"
        assert not _shell_exec_fires(src)

    def test_scoped_spawn_inside_crossbeam_scope(self) -> None:
        """The crossbeam scoped-thread form."""
        src = "crossbeam::thread::scope(|s| {\n    s.spawn(|_| worker());\n}).unwrap();\n"
        assert not _shell_exec_fires(src)


# ---------------------------------------------------------------------------
# The FN half — genuine process spawns must STILL fire (load-bearing)
# ---------------------------------------------------------------------------
class TestRealProcessSpawnsStillFire:
    """A discriminator that cleared these would be a security regression."""

    def test_command_new_sh_dash_c(self) -> None:
        """`Command::new("sh").arg("-c")` is the dangerous shell form."""
        assert _shell_exec_fires('Command::new("sh").arg("-c").arg(payload).spawn()?;')

    def test_command_new_bash_multiline_chain(self) -> None:
        """The same chain split across lines must still fire."""
        src = 'let mut c = Command::new("bash")\n    .arg("-c")\n    .arg(user_input)\n    .spawn()?;\n'
        assert _shell_exec_fires(src)

    def test_std_process_command(self) -> None:
        """A fully-qualified `std::process::Command` with an inline-shell flag."""
        assert _shell_exec_fires('std::process::Command::new(prog).arg("-c").spawn()?;')

    def test_bare_receiver_spawn_with_command_head(self) -> None:
        """`cmd.spawn()` where `cmd` is a Command — the scoped-shape trap.

        This is why the scoped clear requires a scope OPENER and bails on a
        Command in the look-back: the receiver alone looks identical.
        """
        src = 'let mut cmd = Command::new("sh");\ncmd.arg("-c").arg(x);\ncmd.spawn()?;\n'
        assert _shell_exec_fires(src)

    def test_powershell_dash_c(self) -> None:
        """The Windows shell form."""
        assert _shell_exec_fires('Command::new("powershell").arg("-c").arg(s).spawn()?;')

    def test_thread_spawn_beside_a_real_command_still_fires(self) -> None:
        """A line carrying BOTH must never be cleared by the in-process rule."""
        assert _shell_exec_fires('std::thread::spawn(move || { Command::new("sh").arg("-c").spawn(); });')
