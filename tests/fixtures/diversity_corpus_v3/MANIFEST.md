# diversity-corpus-v3 — 17-plugin FP-elimination harness

This file lists the 17 third-party Claude Code plugins used as the
**v3 diversity corpus** for systematic false-positive elimination in
`scripts/validate_security.py`.

The plugins are NOT vendored under `tests/fixtures/diversity_corpus_v3/`
— they live under `~/Code/diversity-plugins-v3/<owner>__<repo>/` on
the developer's machine. This manifest pins the GitHub source + HEAD
commit each scan was taken against, so the corpus can be regenerated
with `git clone <url> --depth=1 && git checkout <sha>` for each entry.

The corpus targets a wide diversity of plugin styles:

- **Single-skill micro-plugins** (davegoldblatt, zscole)
- **Marketplace hubs** (Dev-GOM, takahirom)
- **Mega skill catalogues** (wshobson, ed3dai, shinpr)
- **Workflow / orchestration plugins** (gmickel, nyldn, tzachbon, umputun)
- **Domain-specific plugins** (anthropics life-sciences, iPlug3 audio,
  timescale pg-aiguide)
- **Hook-heavy plugins with extensive shell scripts** (rohitg00, rsmdt,
  thedotmack)

| Plugin | GitHub URL | HEAD SHA |
|---|---|---|
| anthropics__life-sciences | https://github.com/anthropics/life-sciences | 20f100e |
| davegoldblatt__total-recall | https://github.com/davegoldblatt/total-recall | c04b4ec |
| Dev-GOM__claude-code-marketplace | https://github.com/Dev-GOM/claude-code-marketplace | eed6aa2 |
| ed3dai__ed3d-plugins | https://github.com/ed3dai/ed3d-plugins | 47257b5 |
| gmickel__flow-next | https://github.com/gmickel/flow-next | 3675d7a |
| iPlug3__audio-plugin-dev-skills | https://github.com/iPlug3/audio-plugin-dev-skills | e593a80 |
| nyldn__claude-octopus | https://github.com/nyldn/claude-octopus | 65698f6 |
| rohitg00__pro-workflow | https://github.com/rohitg00/pro-workflow | 9df6af8 |
| rsmdt__the-startup | https://github.com/rsmdt/the-startup | 4156e0c |
| shinpr__claude-code-workflows | https://github.com/shinpr/claude-code-workflows | a5ce193 |
| takahirom__takahirom-claude-code-marketplace | https://github.com/takahirom/takahirom-claude-code-marketplace | f3b1355 |
| thedotmack__claude-mem | https://github.com/thedotmack/claude-mem | 28b40c0 |
| timescale__pg-aiguide | https://github.com/timescale/pg-aiguide | 0083aa8 |
| tzachbon__smart-ralph | https://github.com/tzachbon/smart-ralph | 1b33202 |
| umputun__cc-thingz | https://github.com/umputun/cc-thingz | c0337e3 |
| wshobson__agents | https://github.com/wshobson/agents | c15b108 |
| zscole__adversarial-spec | https://github.com/zscole/adversarial-spec | f90cf0c |

## Reproducing the scan

```bash
mkdir -p ~/Code/diversity-plugins-v3
cd ~/Code/diversity-plugins-v3
# Clone all 17 plugins
while IFS='|' read -r name url sha; do
  [ -d "$name" ] || git clone --depth=1 "$url" "$name"
  (cd "$name" && git fetch --depth=1 origin "$sha" 2>/dev/null && git checkout "$sha")
done <<'EOF'
anthropics__life-sciences|https://github.com/anthropics/life-sciences|20f100e
davegoldblatt__total-recall|https://github.com/davegoldblatt/total-recall|c04b4ec
Dev-GOM__claude-code-marketplace|https://github.com/Dev-GOM/claude-code-marketplace|eed6aa2
ed3dai__ed3d-plugins|https://github.com/ed3dai/ed3d-plugins|47257b5
gmickel__flow-next|https://github.com/gmickel/flow-next|3675d7a
iPlug3__audio-plugin-dev-skills|https://github.com/iPlug3/audio-plugin-dev-skills|e593a80
nyldn__claude-octopus|https://github.com/nyldn/claude-octopus|65698f6
rohitg00__pro-workflow|https://github.com/rohitg00/pro-workflow|9df6af8
rsmdt__the-startup|https://github.com/rsmdt/the-startup|4156e0c
shinpr__claude-code-workflows|https://github.com/shinpr/claude-code-workflows|a5ce193
takahirom__takahirom-claude-code-marketplace|https://github.com/takahirom/takahirom-claude-code-marketplace|f3b1355
thedotmack__claude-mem|https://github.com/thedotmack/claude-mem|28b40c0
timescale__pg-aiguide|https://github.com/timescale/pg-aiguide|0083aa8
tzachbon__smart-ralph|https://github.com/tzachbon/smart-ralph|1b33202
umputun__cc-thingz|https://github.com/umputun/cc-thingz|c0337e3
wshobson__agents|https://github.com/wshobson/agents|c15b108
zscole__adversarial-spec|https://github.com/zscole/adversarial-spec|f90cf0c
EOF

# Then run the parallel security scan from the CPV repo:
cd /path/to/claude-plugins-validation
ls -d ~/Code/diversity-plugins-v3/*/ | xargs -P 6 -I{} bash -c '
  name=$(basename "{}")
  CPV_SKIP_GITHUB_INTEGRITY=1 timeout 120 \
    uv run scripts/validate_security.py "{}" \
    > "/tmp/cpv-v3-scans/${name}.txt" 2>&1
'
```

## FP-elimination history

Predicates added against this corpus (one commit per predicate):

| Commit | Predicate | FPs eliminated |
|---|---|---|
| b315bcc | RC-113 escape-sequence skip extended to all c-style-string langs (C#, Java, Kotlin, Swift, Go, JSON, shell printf) | 10 CRIT / 4 plugins |
| d187f5f | Pipe-to-shell (RC-114..119) skip when interpreter has explicit file argument; skip inside quoted shell-string literals | 54 CRIT / 5 plugins |
| c464ec0 | RC-121 (`exec <cmd>`) skip when preceded by `-` (find primary `-exec`) | 9 CRIT / 2 plugins |
| a32c43a | Path-traversal (RC-110/112/113/135) skip when line is a shell regex source (`grep -E` / `sed s/` / `awk` / `find -name`) | 11 CRIT + 5 MAJ / 2 plugins |
| c21c087 | RC-145..149 credential-harvest: hyphenated test files + `.example.*` / `.sample.*` template skip | 8 CRIT / 2 plugins |
| e77dc76 | DB connection-string placeholder skip (`username:password@`, `oauth2:${TOKEN}@`, `f"://{u}:{p}@"`) | 23 CRIT / 5 plugins |

Cumulative impact: **~115 CRIT FPs eliminated across 5+ plugins** (baseline
259 → 144). Bench (TP/FP classifier) stayed at **6 rules × 100/100**
throughout. Full pytest suite stayed green at **3604+ tests**.
