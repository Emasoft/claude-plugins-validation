# Per-platform delivery (what the binaries cost every user)

## Table of Contents

- [Overview](#overview)
- [The measurement that started this](#the-measurement-that-started-this)
- [Shape A — fetch-on-install (preferred where a network exists)](#shape-a--fetch-on-install-preferred-where-a-network-exists)
- [Shape B — commit-all-platforms (offline-correct)](#shape-b--commit-all-platforms-offline-correct)
- [The tradeoff, side by side](#the-tradeoff-side-by-side)
- [Neither is a security downgrade — provided the checksum is verified](#neither-is-a-security-downgrade--provided-the-checksum-is-verified)
- [The two findings](#the-two-findings)

## Overview

The compiled-component canon says a plugin ships ONLY its built binaries under
`bin/`. It does not say HOW those binaries reach the user, and that second
question turns out to dominate install size. This reference documents the two
compliant delivery shapes, states which to prefer, and names the one variant
that is genuinely unsafe.

Read this together with
[compiled-component-canon.md](compiled-component-canon.md), which governs WHAT
ships; this document governs HOW it is delivered.

## The measurement that started this

Issue #185 §1, measured on a real install of `perfect-skill-suggester` 3.10.10
on a `Darwin/arm64` host:

| Component | Size | Share |
| --- | --- | --- |
| Total install | 160 MB | 100% |
| `bin/` (11 binaries + 1 dispatcher) | 154 MB | 96% |
| `rust/` compile source | 1.7 MB | 1.06% |

That host can execute exactly two of the eleven binaries — 28.2 MB. **125.8 MB,
79% of the entire install, is native code the machine can never run.**

The number is the whole argument. Stripping the compile source — the thing the
canon was written to enforce — saves about 1%. The multi-platform binary payload
the same canon mandates costs about 79%. Solve delivery first; if you do both,
do them in that order.

## Shape A — fetch-on-install (preferred where a network exists)

The plugin commits a small dispatcher and no platform binaries. The GitHub
release carries one asset per target plus a `SHA256SUMS` manifest. On first run
(or at install time) the dispatcher resolves the host triple, downloads only
that asset, verifies it against the recorded digest, and refuses to execute on a
mismatch.

The committed tree:

```text
bin/
  mytool              # dispatcher: resolve host triple -> fetch -> verify -> exec
  SHA256SUMS          # optional: pinned digests, committed alongside the dispatcher
```

The release assets:

```text
mytool-darwin-arm64
mytool-darwin-x86_64
mytool-linux-arm64
mytool-linux-x86_64
mytool-windows-x86_64.exe
SHA256SUMS
```

The install step, in sketch form — download, verify, and only then use:

```text
curl -fsSL -o "$dest" "$release_url/mytool-$os-$arch"
curl -fsSL -o "$dest.sha256" "$release_url/SHA256SUMS"
sha256sum -c "$dest.sha256" || exit 1
```

Properties:

- **Smallest install.** The user carries one binary, not N.
- **Requires a network at install time.** An air-gapped or
  restricted-egress host cannot complete the fetch.
- **The download must be verified.** See the section below — this is not
  optional, and an unverified fetch is the one shape this document rejects.
- **The failure mode must be loud.** A checksum mismatch, an unsupported
  triple, or a failed download exits with a clear error; it never silently
  falls back to an unverified copy.

CPV recognises this shape through
`validate_plugin._has_release_asset_installer`, which requires BOTH a
release-asset download AND a sha256 verification step in the SAME installer
script. A download without its checksum does not qualify, deliberately.

## Shape B — commit-all-platforms (offline-correct)

The plugin commits every `bin/<tool>-<os>-<arch>` artifact plus a dispatcher
that selects among them. This is the shape the canon has described until now,
and it stays valid.

Properties:

- **Works offline.** Everything needed is in the installed tree; no network,
  no egress allowlist, no release availability at install time.
- **Every user pays for every platform.** The dead payload grows linearly with
  the target matrix and is charged to each install.
- **The bytes are reviewable in place.** They are committed, so they can be
  hashed, pinned, and attested (`cpv.attest[]`, issue #185 §2/§3).

Choose this when installs must work without a network, when the release host is
not reliably reachable from the install environment, or when the total binary
payload is small enough that the waste does not matter.

## The tradeoff, side by side

| | Fetch-on-install | Commit-all-platforms |
| --- | --- | --- |
| Install size | One binary | Every binary |
| Works offline | No | Yes |
| Network at install | Required | Not required |
| Bytes reviewable in the repo | Dispatcher + digests | Full artifacts |
| Failure mode | Loud: download or checksum fails | None at install |
| Attestation (`cpv.attest[]`) | Applies to the pinned digests | Applies to the committed artifacts |

**Prefer fetch-on-install where a network is available at install time.** It is
the shape that fixes the measured 79%, and its cost — a network dependency at
one moment — is bounded and visible. Prefer commit-all-platforms when it is not.

## Neither is a security downgrade — provided the checksum is verified

A verified fetch and a committed binary give the same guarantee by different
means. The committed artifact is pinned by being in the tree; the fetched
artifact is pinned by its recorded digest. In both cases a reviewer can name the
exact bytes that will execute, and in both cases `cpv.attest[]` can tie those
bytes to a source commit, a toolchain, and a build command.

**An UNVERIFIED download is strictly worse than committing the binary, and must
never be shipped.** It replaces an artifact anyone can hash and pin with one
fetched at install time from a mutable remote, so:

- the bytes that execute are whatever the remote served at that moment;
- nothing ties them to any review, any digest, or any source revision;
- a compromised release, a hijacked host, or an intercepted connection
  substitutes code silently, on every install, with no local trace;
- attestation becomes meaningless — there is no recorded digest to attest to.

So the ranking is: verified fetch-on-install, then commit-all-platforms, and
unverified fetch is not on the list at all. If a checksum cannot be verified for
some reason, commit the binaries instead — do not ship the fetch.

## The two findings

Both are emitted by `scripts/cpv_platform_delivery.py`. Neither blocks: WARNING
is the only non-blocking tier under `--strict`, and INFO does not count.

| Rule | Severity | Fires when |
| --- | --- | --- |
| `RC-PLATFORM-BLOAT` | WARNING | `bin/` ships at least 3 platform variants AND more than half the shipped binary bytes cannot run on the scanning host. The message carries the real MB and %. |
| `RC-PLATFORM-DELIVERY-OK` | INFO | The plugin ships the fetch-on-install shape — a checksum-verified release-asset installer — and is not carrying a bloated committed payload. |

`RC-PLATFORM-BLOAT` is advisory by design. Committing every platform is a
legitimate, offline-correct choice that the canon itself recommended, so a
blocking rule would retro-break every compiled plugin in the fleet for following
documented guidance. The finding reports the measurement and lets the author
weigh it.

Two limits are worth stating plainly, because a number that overstates itself is
worse than no number:

- The fraction is priced against ONE host — the machine running the scan. It is
  not a claim about the fleet.
- A binary whose shipped filename carries no recognised platform (a `.wasm`, a
  hand-named artifact) counts toward the total but never toward the unusable
  bytes. The reported waste is therefore a floor, not a ceiling.
