#!/usr/bin/env python3
"""Generate per-environment .tfvars files and an HTML parameter viewer
from a params.yaml SSOT sheet, for ANY Terraform module.

params.yaml is the single source of truth (SSOT) for a module's
per-environment values. This script:

  1. validates params.yaml against params.schema.json (using the Python
     ``jsonschema`` package when available; otherwise it runs equivalent
     built-in structural checks and says so),
  2. cross-checks the sheet against variables.tf — every parameter in the
     sheet must exist as a variable, and every variable assigned in any
     generated tfvars file must be present in the sheet (the
     design<->implementation drift check),
  3. renders the tfvars files and the human-readable HTML matrix.

Usage:
    python3 generate.py [--tf-dir DIR]           # regenerate in place
    python3 generate.py [--tf-dir DIR] --check   # CI/pre-commit gate:
                                                 # exit non-zero if the
                                                 # committed outputs differ

Path options (defaults reproduce the standard layout, relative to
--tf-dir, which itself defaults to the current directory):
    --params PATH     params.yaml            (default: <tf-dir>/params.yaml)
    --schema PATH     params.schema.json     (default: next to params.yaml;
                                              skipped if the file is absent)
    --variables PATH  variables.tf           (default: <tf-dir>/variables.tf)
    --tfvars-dir DIR  output dir for tfvars  (default: <tf-dir>/environments)
    --viewer PATH     viewer HTML output     (default: <tf-dir>/viewer/params.html)
    --title NAME      project name shown in the viewer
                      (default: the --tf-dir directory name)

Environment names are never hardcoded: they come from the top-level
``environments`` list in params.yaml, in that order.

Generation is deterministic (stable ordering, no timestamps), so --check
is a meaningful byte-for-byte comparison.
"""

from __future__ import annotations

import argparse
import difflib
import html
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("error: PyYAML is required (pip install pyyaml)")

TYPES = ("string", "number", "bool", "map(string)", "list(string)", "any")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class Config:
    """Resolved file locations for one run."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.tf_dir = Path(args.tf_dir).resolve()
        self.params_file = (
            Path(args.params).resolve() if args.params else self.tf_dir / "params.yaml"
        )
        if args.schema:
            self.schema_file: Path | None = Path(args.schema).resolve()
        else:
            candidate = self.params_file.parent / "params.schema.json"
            self.schema_file = candidate if candidate.exists() else None
        self.variables_file = (
            Path(args.variables).resolve()
            if args.variables
            else self.tf_dir / "variables.tf"
        )
        self.tfvars_dir = (
            Path(args.tfvars_dir).resolve()
            if args.tfvars_dir
            else self.tf_dir / "environments"
        )
        self.viewer_file = (
            Path(args.viewer).resolve()
            if args.viewer
            else self.tf_dir / "viewer" / "params.html"
        )
        self.title = args.title or self.tf_dir.name

    def display(self, path: Path) -> str:
        """Path relative to the module dir when possible (for messages)."""
        try:
            return str(path.relative_to(self.tf_dir))
        except ValueError:
            return str(path)


# --------------------------------------------------------------------------
# Loading + validation of params.yaml
# --------------------------------------------------------------------------

def load_params(cfg: Config) -> dict:
    with cfg.params_file.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError("params.yaml: top level must be a mapping")
    return data


def _type_ok(value, tf_type: str) -> bool:
    """Does a Python value match the declared Terraform type?"""
    if tf_type == "any":
        return True
    if tf_type == "string":
        return value is None or isinstance(value, str)
    if tf_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if tf_type == "bool":
        return isinstance(value, bool)
    if tf_type == "map(string)":
        return isinstance(value, dict) and all(
            isinstance(k, str) and isinstance(v, str) for k, v in value.items()
        )
    if tf_type == "list(string)":
        return isinstance(value, list) and all(isinstance(v, str) for v in value)
    return False


def basic_schema_checks(data: dict) -> list[str]:
    """Structural checks equivalent to params.schema.json.

    Used when the jsonschema package is unavailable (or no schema file is
    present); also run (cheaply) for the rules a static schema cannot
    express.
    """
    errors: list[str] = []
    envs = data.get("environments")
    if not isinstance(envs, list) or not envs:
        errors.append("environments: must be a non-empty list")
        envs = []
    if len(set(envs)) != len(envs):
        errors.append("environments: duplicate entries")
    groups = data.get("groups", {})
    if not isinstance(groups, dict):
        errors.append("groups: must be a mapping of group name -> {label_ja}")
    else:
        for gname, ginfo in groups.items():
            if (
                not isinstance(ginfo, dict)
                or not isinstance(ginfo.get("label_ja"), str)
                or not ginfo.get("label_ja")
            ):
                errors.append(f"groups.{gname}: must define a non-empty label_ja string")
    params = data.get("parameters")
    if not isinstance(params, list) or not params:
        errors.append("parameters: must be a non-empty list")
        return errors

    seen: set[str] = set()
    for idx, p in enumerate(params):
        where = f"parameters[{idx}]"
        if not isinstance(p, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        name = p.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name or ""):
            errors.append(f"{where}.name: missing or not a valid variable name")
            continue
        where = f"parameter '{name}'"
        if name in seen:
            errors.append(f"{where}: duplicate parameter name")
        seen.add(name)
        for field in ("group", "description"):
            if not isinstance(p.get(field), str) or not p[field]:
                errors.append(f"{where}.{field}: must be a non-empty string")
        if "description_ja" in p and (
            not isinstance(p["description_ja"], str) or not p["description_ja"]
        ):
            errors.append(f"{where}.description_ja: must be a non-empty string when present")
        tf_type = p.get("type")
        if tf_type not in TYPES:
            errors.append(f"{where}.type: must be one of {', '.join(TYPES)}")
            tf_type = "any"
        if "default" not in p:
            errors.append(f"{where}.default: required (use null when the variable defaults to null)")
        elif not _type_ok(p["default"], tf_type):
            errors.append(f"{where}.default: does not match declared type {tf_type}")
        values = p.get("values", {})
        if not isinstance(values, dict):
            errors.append(f"{where}.values: must be a mapping of environment -> value")
            continue
        for env, val in values.items():
            if env not in envs:
                errors.append(f"{where}.values.{env}: not in environments {envs}")
            if not _type_ok(val, tf_type):
                errors.append(f"{where}.values.{env}: does not match declared type {tf_type}")
    return errors


def validate_params(cfg: Config, data: dict) -> list[str]:
    """Validate against params.schema.json when possible, else basic checks."""
    if cfg.schema_file is None:
        print("note: no params.schema.json found next to params.yaml; "
              "running built-in structural checks only")
        return basic_schema_checks(data)
    try:
        import jsonschema  # type: ignore
    except ImportError:
        print("note: python jsonschema package not available; "
              "running built-in structural checks equivalent to params.schema.json")
        return basic_schema_checks(data)

    import json

    schema = json.loads(cfg.schema_file.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    ]
    # Rules a static schema cannot express (dynamic environment names,
    # duplicate parameter names) are enforced regardless of the
    # validation path.
    errors.extend(basic_schema_checks(data))
    return errors


# --------------------------------------------------------------------------
# variables.tf / tfvars parsing (for the drift cross-check)
# --------------------------------------------------------------------------

def _scan_block(text: str, start: int) -> tuple[str, int]:
    """Return (body, end) of an HCL block whose '{' is just before start."""
    depth, i, in_str = 1, start, False
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i], i + 1
        i += 1
    raise ValueError("unbalanced braces while parsing variables.tf")


def _unescape(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            out.append({'"': '"', "\\": "\\", "n": "\n", "t": "\t"}.get(nxt, nxt))
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _parse_literal(raw: str):
    """Interpret a single-line HCL literal (string/number/bool/null/{})."""
    raw = raw.strip()
    if raw.startswith('"'):
        m = re.match(r'"((?:[^"\\]|\\.)*)"', raw)
        return _unescape(m.group(1)) if m else raw
    token = raw.split()[0] if raw.split() else raw
    if token == "null":
        return None
    if token == "true":
        return True
    if token == "false":
        return False
    if token == "{}":
        return {}
    if token == "[]":
        return []
    try:
        return int(token)
    except ValueError:
        try:
            return float(token)
        except ValueError:
            return token  # expression or complex literal: compare as text


def parse_variables_tf(cfg: Config) -> dict[str, dict]:
    text = cfg.variables_file.read_text(encoding="utf-8")
    variables: dict[str, dict] = {}
    for m in re.finditer(r'variable\s+"([^"]+)"\s*\{', text):
        name = m.group(1)
        body, _ = _scan_block(text, m.end())
        info: dict = {}
        d = re.search(r'description\s*=\s*"((?:[^"\\]|\\.)*)"', body, re.S)
        if d:
            info["description"] = _unescape(d.group(1))
        t = re.search(r"^\s*type\s*=\s*(.+)$", body, re.M)
        if t:
            info["type"] = t.group(1).strip()
        df = re.search(r"^\s*default\s*=\s*(.+)$", body, re.M)
        if df:
            info["default"] = _parse_literal(df.group(1))
        variables[name] = info
    return variables


def parse_tfvars_assignments(path: Path) -> set[str]:
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if m:
            names.add(m.group(1))
    return names


def cross_check(cfg: Config, data: dict, variables: dict[str, dict]) -> tuple[list[str], list[str]]:
    """The design<->implementation drift check. Returns (errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []
    sheet = {p["name"]: p for p in data["parameters"]}

    for name in sheet:
        if name not in variables:
            errors.append(f"params.yaml: '{name}' has no matching variable in variables.tf")

    used: set[str] = set()
    for env in data["environments"]:
        tfvars = cfg.tfvars_dir / f"{env}.tfvars"
        if tfvars.exists():
            used |= parse_tfvars_assignments(tfvars)
    for name in sorted(used):
        if name not in sheet:
            errors.append(
                f"drift: variable '{name}' is assigned in {cfg.display(cfg.tfvars_dir)}/*.tfvars "
                "but missing from params.yaml"
            )

    for name, p in sheet.items():
        tf = variables.get(name)
        if not tf:
            continue
        if tf.get("type") and tf["type"] != p["type"]:
            warnings.append(f"'{name}': type differs (params.yaml: {p['type']}, variables.tf: {tf['type']})")
        if tf.get("description") and tf["description"].strip() != str(p["description"]).strip():
            warnings.append(f"'{name}': description differs between params.yaml and variables.tf")
        if "default" in tf and tf["default"] != p.get("default"):
            warnings.append(
                f"'{name}': default differs (params.yaml: {p.get('default')!r}, variables.tf: {tf['default']!r})"
            )

    not_in_sheet = sorted(set(variables) - set(sheet))
    if not_in_sheet:
        warnings.append(
            "variables not covered by params.yaml (they always use their default): "
            + ", ".join(not_in_sheet)
        )
    return errors, warnings


# --------------------------------------------------------------------------
# HCL rendering
# --------------------------------------------------------------------------

def hcl_value(value, indent: int = 0) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, dict):
        if not value:
            return "{}"
        pad = " " * indent
        width = max(len(str(k)) for k in value)
        lines = ["{"]
        for key in sorted(value, key=str):
            lines.append(f"{pad}  {str(key).ljust(width)} = {hcl_value(value[key], indent + 2)}")
        lines.append(pad + "}")
        return "\n".join(lines)
    if isinstance(value, list):
        return "[" + ", ".join(hcl_value(v, indent) for v in value) + "]"
    raise TypeError(f"cannot render {type(value)} as HCL")


TFVARS_HEADER = """\
# =============================================================================
# {env}.tfvars -- GENERATED from params.yaml by tools/generate.py.
# Do not edit this file by hand: edit params.yaml (the SSOT) and re-run
#   python3 tools/generate.py
# =============================================================================
"""


def render_tfvars(data: dict, env: str) -> str:
    groups: list[str] = []
    by_group: dict[str, list[dict]] = {}
    for p in data["parameters"]:
        if env in (p.get("values") or {}):
            if p["group"] not in by_group:
                by_group[p["group"]] = []
                groups.append(p["group"])
            by_group[p["group"]].append(p)

    out = [TFVARS_HEADER.format(env=env)]
    for group in groups:
        entries = by_group[group]
        out.append(f"\n# --- {group} ---\n")
        # terraform fmt aligns the `=` of consecutive single-line assignments;
        # a multi-line value (e.g. a map) is not aligned with its neighbours
        # and breaks the alignment run.
        run: list[tuple[str, str]] = []

        def flush() -> None:
            if run:
                width = max(len(name) for name, _ in run)
                for name, rendered in run:
                    out.append(f"{name.ljust(width)} = {rendered}\n")
                run.clear()

        for p in entries:
            rendered = hcl_value(p["values"][env])
            if "\n" in rendered:
                flush()
                out.append(f"{p['name']} = {rendered}\n")
            else:
                run.append((p["name"], rendered))
        flush()
    return "".join(out)


# --------------------------------------------------------------------------
# HTML viewer rendering
# --------------------------------------------------------------------------

def display_value(value) -> str:
    if value is None:
        return '<span class="null">null</span>'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        if not value:
            return "{}"
        return "<br>".join(
            f"{html.escape(str(k))} = {html.escape(str(v))}" for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
        )
    if isinstance(value, list):
        return ", ".join(html.escape(str(v)) for v in value)
    return html.escape(str(value))


CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, "Segoe UI", Roboto, "Hiragino Sans",
       "Noto Sans JP", sans-serif; color: #1c2330; background: #f6f7f9; }
header { position: sticky; top: 0; z-index: 3; background: #fff;
         border-bottom: 1px solid #d9dee6; padding: 14px 20px 10px; }
h1 { font-size: 19px; margin: 0 0 4px; }
.sub { margin: 0 0 8px; color: #5a6577; font-size: 13px; }
.legend { font-size: 12.5px; color: #5a6577; display: flex; gap: 16px; flex-wrap: wrap; }
.legend .swatch { display: inline-block; width: 11px; height: 11px; border-radius: 2px;
                  vertical-align: -1px; margin-right: 4px; border: 1px solid #c8cfda; }
#filter { margin-top: 10px; width: min(480px, 100%); padding: 7px 10px; font-size: 14px;
          border: 1px solid #c8cfda; border-radius: 6px; }
main { padding: 16px 20px 40px; overflow-x: auto; }
table { border-collapse: collapse; width: 100%; background: #fff;
        box-shadow: 0 1px 3px rgba(16,24,40,.08); border-radius: 8px; overflow: hidden; }
th, td { border-bottom: 1px solid #e4e8ef; padding: 10px 14px; text-align: left;
         vertical-align: top; font-size: 14px; }
thead th { position: sticky; top: 118px; background: #eef1f6; z-index: 2;
           font-size: 12.5px; letter-spacing: .04em; color: #44506a; }
thead th:first-child { left: 0; z-index: 3; }
tr.group td { background: #33415c; color: #fff; font-weight: 600; font-size: 13.5px;
              letter-spacing: .03em; padding-top: 12px; padding-bottom: 12px; }
tbody tr.param.alt > td { background-color: #f7f9fc; }
tbody tr.param > td.val.diff { background-color: #fff1c9; }
td.name { position: sticky; left: 0; z-index: 1; background-color: #fff;
          min-width: 180px; }
td.name code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
               font-size: 13.5px; font-weight: 600; }
td.name .type { color: #8a94a6; font-size: 11.5px; margin-top: 2px;
                font-family: ui-monospace, Menlo, monospace; }
td.desc { color: #3d4657; font-size: 13px; line-height: 1.6;
          min-width: 280px; max-width: 520px; }
td.val { white-space: nowrap; font-variant-numeric: tabular-nums; }
td.val.is-default { color: #98a1b1; }
td.val .inh { display: block; font-size: 10.5px; color: #a4adbd; }
.null { font-style: italic; }
footer { color: #8a94a6; font-size: 12px; padding: 0 20px 24px; }
"""

JS = """
const input = document.getElementById('filter');
input.addEventListener('input', () => {
  const q = input.value.trim().toLowerCase();
  document.querySelectorAll('tr.param').forEach(tr => {
    tr.style.display = (!q || tr.dataset.search.includes(q)) ? '' : 'none';
  });
  document.querySelectorAll('tr.group').forEach(gr => {
    let el = gr.nextElementSibling, any = false;
    while (el && !el.classList.contains('group')) {
      if (el.classList.contains('param') && el.style.display !== 'none') any = true;
      el = el.nextElementSibling;
    }
    gr.style.display = any ? '' : 'none';
  });
});
"""


def render_html(cfg: Config, data: dict) -> str:
    envs: list[str] = data["environments"]
    groups: list[str] = []
    by_group: dict[str, list[dict]] = {}
    for p in data["parameters"]:
        if p["group"] not in by_group:
            by_group[p["group"]] = []
            groups.append(p["group"])
        by_group[p["group"]].append(p)

    group_labels = {
        name: (info or {}).get("label_ja") or name
        for name, info in (data.get("groups") or {}).items()
    }
    rows: list[str] = []
    row_index = 0
    for group in groups:
        label = group_labels.get(group, group)
        rows.append(f'<tr class="group"><td colspan="{2 + len(envs)}">{html.escape(label)}</td></tr>')
        for p in by_group[group]:
            values = p.get("values") or {}
            default = p.get("default")
            effective = {env: values.get(env, default) for env in envs}
            differs = len({repr(v) for v in effective.values()}) > 1
            cells = []
            for env in envs:
                eff = effective[env]
                classes = ["val"]
                if differs:
                    classes.append("diff")
                if eff == default:
                    classes.append("is-default")
                cell = display_value(eff)
                if env not in values:
                    cell += '<span class="inh">（デフォルト）</span>'
                cells.append(f'<td class="{" ".join(classes)}">{cell}</td>')
            desc = str(p.get("description_ja") or p["description"])
            search = html.escape(
                f"{p['name']} {p['group']} {label} {p['description']} "
                f"{p.get('description_ja', '')}".lower(),
                quote=True,
            )
            alt = " alt" if row_index % 2 else ""
            row_index += 1
            rows.append(
                f'<tr class="param{alt}" data-search="{search}">'
                f'<td class="name"><code>{html.escape(p["name"])}</code>'
                f'<div class="type">{html.escape(p["type"])}</div></td>'
                f'<td class="desc">{html.escape(desc)}</td>'
                + "".join(cells)
                + "</tr>"
            )

    title = html.escape(cfg.title)
    env_headers = "".join(f"<th>{html.escape(env)}</th>" for env in envs)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>パラメータシート — {title}</title>\n"
        f"<style>{CSS}</style>\n</head>\n<body>\n"
        "<header>\n"
        f"<h1>パラメータシート — {title}</h1>\n"
        '<p class="sub">このページは <code>params.yaml</code>（唯一の正本）から '
        "<code>tools/generate.py</code> で自動生成されています。直接編集せず、YAML を編集してください。"
        "各セルはその環境での<em>実効値</em>を表示します。</p>\n"
        '<div class="legend">'
        '<span><span class="swatch" style="background:#fff1c9"></span>ハイライト = 環境間で値が異なります</span>'
        '<span><span class="swatch" style="background:#fff"></span><span style="color:#98a1b1">薄字</span> = デフォルト値と同じです</span>'
        '<span>（デフォルト） = その環境では未設定のため、デフォルト値が適用されます</span>'
        "</div>\n"
        '<input id="filter" type="search" placeholder="パラメータ名やキーワードで絞り込み…" aria-label="パラメータの絞り込み">\n'
        "</header>\n<main>\n<table>\n<thead><tr>"
        f"<th>パラメータ</th><th>説明</th>{env_headers}</tr></thead>\n<tbody>\n"
        + "\n".join(rows)
        + "\n</tbody>\n</table>\n</main>\n"
        "<footer>正本： params.yaml ・ 再生成： python3 tools/generate.py ・ "
        "乖離チェック： python3 tools/generate.py --check</footer>\n"
        f"<script>{JS}</script>\n</body>\n</html>\n"
    )


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def build_outputs(cfg: Config, data: dict) -> dict[Path, str]:
    outputs: dict[Path, str] = {}
    for env in data["environments"]:
        outputs[cfg.tfvars_dir / f"{env}.tfvars"] = render_tfvars(data, env)
    outputs[cfg.viewer_file] = render_html(cfg, data)
    return outputs


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="generate.py",
        description="Generate tfvars + HTML viewer from a params.yaml SSOT sheet.",
    )
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 if committed outputs differ from generated ones")
    ap.add_argument("--tf-dir", default=".",
                    help="Terraform module directory (default: current directory)")
    ap.add_argument("--params", help="path to params.yaml (default: <tf-dir>/params.yaml)")
    ap.add_argument("--schema", help="path to params.schema.json (default: next to params.yaml)")
    ap.add_argument("--variables", help="path to variables.tf (default: <tf-dir>/variables.tf)")
    ap.add_argument("--tfvars-dir", help="tfvars output directory (default: <tf-dir>/environments)")
    ap.add_argument("--viewer", help="viewer HTML output path (default: <tf-dir>/viewer/params.html)")
    ap.add_argument("--title", help="project name shown in the viewer (default: <tf-dir> directory name)")
    return ap.parse_args(argv)


def main(argv: list[str]) -> int:
    cfg = Config(parse_args(argv))
    check_mode = "--check" in argv

    try:
        data = load_params(cfg)
    except Exception as exc:  # YAML syntax errors etc.
        print(f"error: cannot load params.yaml: {exc}")
        return 2

    errors = validate_params(cfg, data)
    if errors:
        print("params.yaml failed validation:")
        for e in errors:
            print(f"  - {e}")
        return 2

    variables = parse_variables_tf(cfg)
    drift_errors, warnings = cross_check(cfg, data, variables)
    for w in warnings:
        print(f"warning: {w}")
    if drift_errors:
        print("cross-check against variables.tf / tfvars failed:")
        for e in drift_errors:
            print(f"  - {e}")
        return 2

    outputs = build_outputs(cfg, data)

    if check_mode:
        drifted = False
        for path, expected in sorted(outputs.items(), key=lambda kv: str(kv[0])):
            actual = path.read_text(encoding="utf-8") if path.exists() else ""
            if actual != expected:
                drifted = True
                print(f"DRIFT: {cfg.display(path)} differs from what params.yaml generates:")
                diff = difflib.unified_diff(
                    actual.splitlines(keepends=True),
                    expected.splitlines(keepends=True),
                    fromfile=f"{cfg.display(path)} (committed)",
                    tofile=f"{cfg.display(path)} (generated from params.yaml)",
                )
                sys.stdout.writelines(list(diff)[:80])
        if drifted:
            print("\nRun `generate.py` and commit the regenerated files.")
            return 1
        print(f"check OK: {cfg.display(cfg.tfvars_dir)}/*.tfvars and "
              f"{cfg.display(cfg.viewer_file)} match params.yaml")
        return 0

    for path, content in sorted(outputs.items(), key=lambda kv: str(kv[0])):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text(encoding="utf-8") == content:
            print(f"unchanged: {cfg.display(path)}")
        else:
            path.write_text(content, encoding="utf-8")
            print(f"generated: {cfg.display(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
