# CPVPPC — the publishing-pipeline canon (generated)

This file is GENERATED from `scripts/cpvppc/canon.json`. Do not edit it by
hand — edit the manifest and re-render. A test asserts the committed copy
equals the rendered output.

Canon version: 0.1.0

## Fact types

- `file_present`
- `file_matches_render`
- `json_path_equals`
- `workflow_step`
- `readme_section_current`

Closed for 0.1.0. Later phases extend this list (config_valid, lock_consistent, actions_pinned, ... per the plan); an assertion naming a type absent from this registry is a canon-authoring error and verify.py refuses it rather than passing it silently.

## Assertions

### CPVPPC-MKT-001

- Scope: `marketplace-repo`
- Since: 0.1.0
- Applies when: README.md exists
- Fact: `file_present` {"contains": ["<!-- PLUGIN-VERSIONS-START -->", "<!-- PLUGIN-VERSIONS-END -->"], "path": "README.md"}
- Fix: Insert both marker lines into README.md — START above END, in order — with the plugin-versions table between them.

### CPVPPC-MKT-002

- Scope: `marketplace-repo`
- Since: 0.1.0
- Applies when: scripts/render_readme_table.py exists in the repo
- Fact: `file_matches_render` {"path": "scripts/render_readme_table.py", "rendered_from": "template:scripts/render_readme_table.py"}
- Fix: Replace the repo's renderer with a byte-identical copy of the CPV template templates/scripts/render_readme_table.py, or delete it and let the canon scaffolder install one.

### CPVPPC-MKT-003

- Scope: `marketplace-repo`
- Since: 0.1.0
- Applies when: README.md and .claude-plugin/marketplace.json exist and MKT-002 passed
- Fact: `readme_section_current` {"manifest": ".claude-plugin/marketplace.json", "readme": "README.md"}
- Fix: Run the renderer in write mode (python3 scripts/render_readme_table.py) to regenerate the table from the manifest.

### CPVPPC-MKT-004

- Scope: `marketplace-repo`
- Since: 0.1.0
- Applies when: .github/workflows/ exists
- Fact: `workflow_step` {"gate": true, "step_matches": "render_readme_table.py --check"}
- Fix: Add a CI workflow that runs python3 scripts/render_readme_table.py --check so README-table drift fails the build.

### CPVPPC-MKT-005

- Scope: `marketplace-repo`
- Since: 0.1.0
- Applies when: .github/workflows/ exists
- Fact: `workflow_step` {"gate": false, "not_matches": "--check", "step_matches": "render_readme_table.py"}
- Fix: Add an update workflow step that runs the renderer in write mode (no --check) and commits the regenerated README.

### CPVPPC-MKT-006

- Scope: `marketplace-repo`
- Since: 0.1.0
- Applies when: .claude-plugin/marketplace.json exists
- Fact: `json_path_equals` {"json_path": "version", "path": ".claude-plugin/marketplace.json"}
- Fix: Move the marketplace version to the top-level "version" key of .claude-plugin/marketplace.json — metadata.version is only a legacy alias and is not what the resolver reads.

### CPVPPC-MKT-007

- Scope: `marketplace-repo`
- Since: 0.1.0
- Applies when: always
- Fact: `workflow_step` {"resolves_against_manifest": true, "step_matches": "client_payload.plugin"}
- Fix: P5: the update workflow must resolve the notified plugin identity against .claude-plugin/marketplace.json entries and fail loudly on an unknown one, so a repository_dispatch naming a plugin that is not listed never updates silently.
