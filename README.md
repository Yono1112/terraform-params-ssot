# terraform-params-ssot

**A YAML parameter sheet as the single source of truth (SSOT) for a
Terraform module's per-environment values** — generating the `.tfvars`
files and a human-readable HTML viewer from that one sheet, with a
byte-exact drift check for CI.

![concept](https://img.shields.io/badge/SSOT-params.yaml-blue)

```
params.yaml ──► generate.py ──► environments/<env>.tfvars   (for Terraform)
 (only file      │              viewer/params.html           (for humans)
  you edit)      └─ cross-checks variables.tf (drift detection)
```

## Why

Parameter sheets for infrastructure tend to live in a spreadsheet, while
the actual values live in `.tfvars` files. The two drift apart: the sheet
says one thing, the code does another, and nobody notices until an
incident review. This tool removes the second copy:

- **The YAML sheet is the only place values live.** tfvars files and the
  HTML viewer are *generated artifacts* — deterministic, byte-for-byte
  reproducible, never edited by hand.
- **Value changes are mechanical.** Changing a value in any environment
  is a one-line YAML edit plus a script run. No code changes, no agent,
  no judgement calls — so they cannot drift.
- **Schema changes are the only real code work.** Adding a *new*
  parameter means a human (or a coding agent) adds the variable to
  `variables.tf`, wires it into the resources, and adds one entry to the
  sheet. That is deliberately the only step that touches code.
- **Drift is detected, not hoped away.** `generate.py --check`
  regenerates everything and byte-compares it with the committed files,
  and the generator cross-checks the sheet against `variables.tf`
  (parameter exists? type / description / default in sync? any variable
  assigned in tfvars but missing from the sheet?). Run it in pre-commit
  or CI and divergence fails the build.

## The sheet: `params.yaml`

```yaml
environments:            # environment names live here — nothing is hardcoded
  - dev
  - prod

groups:                  # optional Japanese labels for the viewer
  Lambda:
    label_ja: Lambda

parameters:
  - name: lambda_memory_size     # must match a variable in variables.tf
    group: Lambda
    type: number                 # string | number | bool | map(string) | list(string) | any
    description: "Lambda memory size in MB (128 - 10240)."
    description_ja: "Lambda のメモリサイズ（MB、128〜10240）。"   # optional; viewer only
    default: 256                 # the variable's default in variables.tf
    values:                      # per-environment overrides
      dev: 128
      prod: 512                  # environments not listed use `default`
```

The format is contract-checked against `params.schema.json`
(JSON Schema, draft 2020-12). See [`example/`](example/) for a complete
working sheet with its generated outputs.

## Usage

Copy `generate.py` and `params.schema.json` into your module (e.g. under
`tools/`), write `params.yaml` next to `variables.tf`, then:

```bash
# regenerate tfvars + viewer in place
python3 generate.py --tf-dir path/to/module

# drift gate: exit 1 if committed outputs differ from the sheet
python3 generate.py --tf-dir path/to/module --check
```

### Workflow

1. Edit `params.yaml` — and only `params.yaml`.
2. Run `generate.py`.
3. Commit `params.yaml`, the regenerated `environments/*.tfvars` and
   `viewer/params.html` **together**.

### CLI options

| Option | Default | Meaning |
|---|---|---|
| `--tf-dir DIR` | `.` | Terraform module directory |
| `--params PATH` | `<tf-dir>/params.yaml` | the SSOT sheet |
| `--schema PATH` | next to `params.yaml` | JSON Schema file (skipped if absent) |
| `--variables PATH` | `<tf-dir>/variables.tf` | variables file for the cross-check |
| `--tfvars-dir DIR` | `<tf-dir>/environments` | tfvars output directory |
| `--viewer PATH` | `<tf-dir>/viewer/params.html` | viewer HTML output |
| `--title NAME` | `<tf-dir>` directory name | project name shown in the viewer |
| `--check` | — | verify only; exit 1 on drift |

Environment names, group labels and parameter sets are all read from the
sheet — the tool has no module-specific knowledge. tfvars rendering
follows `terraform fmt` alignment rules, so generated files stay
fmt-clean.

## The drift gate

### pre-commit

Using the [pre-commit](https://pre-commit.com) framework:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: params-ssot-check
        name: params.yaml SSOT drift check
        entry: python3 tools/generate.py --check
        language: system
        pass_filenames: false
        files: ^(params\.yaml|variables\.tf|environments/|viewer/)
```

Or a plain git hook:

```bash
#!/bin/sh
# .git/hooks/pre-commit
python3 tools/generate.py --check || {
  echo "params.yaml drift: run generate.py and commit the results" >&2
  exit 1
}
```

### GitHub Actions

```yaml
name: params-ssot
on: [push, pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install pyyaml jsonschema
      - run: python3 tools/generate.py --check
```

## Requirements

- Python 3.10+
- [PyYAML](https://pypi.org/project/PyYAML/) (required)
- [jsonschema](https://pypi.org/project/jsonschema/) (optional) — when
  it is not installed, `generate.py` runs built-in structural checks
  equivalent to `params.schema.json` and prints a note saying so.

No Terraform installation is needed to run the generator or the check.

## Example

[`example/`](example/) is a self-contained demo module: a 5-parameter
sheet (string / number / bool / `map(string)` / `list(string)`) across 3
groups and 3 environments, its `variables.tf`, and the generated
`environments/*.tfvars` + `viewer/params.html`. Open the viewer in a
browser, then try:

```bash
cd example
python3 ../generate.py --tf-dir .          # regenerate
python3 ../generate.py --tf-dir . --check  # drift gate
```

## Origin

Extracted from the
[terraform-kinesis-pipeline](https://github.com/Yono1112/terraform-kinesis-pipeline)
module, where this sheet drives a Kinesis → Lambda → S3 → SQS pipeline
across dev / stg / prod.
