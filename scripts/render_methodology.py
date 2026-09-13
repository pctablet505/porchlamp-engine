"""Render `docs/METHODOLOGY-v2.md` from the v2 methodology document.

Packet W5-1a (`docs/v2/06-UX-DESIGN.md` section 3 F): the published
methodology page is "rendered from the frozen constants module so it
cannot drift from the code." This script is the one place that turns
:func:`cvi.audit.methodology_history.methodology_with_history` (which
itself is built only from ``cvi.core.constants_v2``, the ``cvi.core.params``
registry, ``cvi.verify.detectors``, the two audit release-bar modules, and
``docs/v2/rfcs/CHANGELOG.md``) into the committed markdown file.

``tests/test_render_methodology.py`` re-renders and diffs against the
committed file byte-for-byte, so CI catches a drift the moment any of
those sources changes without a corresponding ``render_methodology.py``
run -- the doc cannot silently go stale.

Usage::

    PYTHONPATH=$PWD .venv/bin/python scripts/render_methodology.py
    PYTHONPATH=$PWD .venv/bin/python scripts/render_methodology.py --check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from cvi.audit.methodology_history import methodology_with_history

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHANGELOG = REPO_ROOT / "docs" / "v2" / "rfcs" / "CHANGELOG.md"
DEFAULT_OUT = REPO_ROOT / "docs" / "METHODOLOGY-v2.md"
REPRODUCE_COMMAND_TEMPLATE = "cvi reproduce <snapshot dir>"


def _cell(text: Any) -> str:
    """One markdown table cell: pipes and newlines escaped/collapsed so a
    prose value (a criterion sentence, an RFC summary) cannot break the
    table it sits in."""
    return str(text).replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell(value) for value in row) + " |")
    return "\n".join(lines)


def _domain_text(param: dict[str, Any]) -> str:
    domain = param["value_domain"]
    kind = domain["kind"]
    if kind in ("numeric", "ecdf"):
        direction = "higher is better" if domain["higher_is_better"] else "lower is better"
        return f"[{domain['domain_min']:g}, {domain['domain_max']:g}], {direction}"
    if kind == "enum":
        table = domain.get("score_table")
        if table is not None:
            if table and all(isinstance(value, dict) for value in table.values()):
                parts = []
                for regime, scores in table.items():
                    scores_text = ", ".join(
                        f"{persona}={score:g}" for persona, score in scores.items()
                    )
                    parts.append(f"{regime} ({scores_text})")
                return "; ".join(parts)
            return ", ".join(f"{regime}={score:g}" for regime, score in table.items())
        order = domain.get("values_worst_to_best") or []
        if order:
            return " < ".join(order) + " (worst to best)"
        persona_orders = domain.get("persona_orders") or {}
        return "; ".join(
            f"{persona}: " + " < ".join(order) for persona, order in persona_orders.items()
        )
    return ""


def _proxy_text(param: dict[str, Any]) -> str:
    proxy = param.get("proxy")
    if not proxy:
        return ""
    parts = []
    if "proxy_min_n" in proxy:
        parts.append(f"min n {proxy['proxy_min_n']} when the method is a proxy")
    if "proxy_min_signals" in proxy:
        parts.append(f"at least {proxy['proxy_min_signals']} of 10 signals present")
    return "; ".join(parts)


def _render_pillars(document: dict[str, Any]) -> str:
    rows = [[pillar["name"], f"{pillar['weight']:g}"] for pillar in document["pillars"]]
    return _table(["Pillar", "Weight"], rows)


def _render_parameters(document: dict[str, Any]) -> str:
    sections = []
    by_pillar: dict[str, list[dict[str, Any]]] = {}
    for param in document["parameters"]:
        by_pillar.setdefault(param["pillar"], []).append(param)
    for pillar in document["pillars"]:
        params = by_pillar.get(pillar["name"], [])
        if not params:
            continue
        rows = []
        for param in params:
            window = param["window_days"] if param["window_days"] is not None else "\u2014"
            min_n = param["min_n"] if param["min_n"] is not None else "\u2014"
            proxy = _proxy_text(param) or "\u2014"
            rows.append(
                [
                    f"`{param['input_id']}`",
                    f"{param['weight_within_pillar']:g}",
                    min_n,
                    window,
                    param["curve"],
                    _domain_text(param),
                    proxy,
                ]
            )
        table = _table(
            [
                "Input id",
                "Weight within pillar",
                "Min n",
                "Window (days)",
                "Curve",
                "Value domain",
                "Proxy",
            ],
            rows,
        )
        sections.append(
            f"### {pillar['name'].capitalize()} (pillar weight {pillar['weight']:g})\n\n{table}"
        )
    return "\n\n".join(sections)


def _render_gates(document: dict[str, Any]) -> str:
    rows = []
    for gate in document["gates"]:
        cutoffs = (
            ", ".join(
                f"{key}={value:g}" if isinstance(value, (int, float)) else f"{key}={value}"
                for key, value in gate["cutoffs"].items()
            )
            or "\u2014"
        )
        reasons = ", ".join(f"`{reason}`" for reason in gate["reasons"])
        rows.append([f"`{gate['id']}`", gate["name"], gate["criterion"], cutoffs, reasons])
    return _table(["Id", "Name", "Criterion", "Cutoffs", "Reason code(s)"], rows)


def _render_missingness(document: dict[str, Any]) -> str:
    missingness = document["missingness"]
    reasons_table = _table(
        ["Reason", "Semantics"],
        [[f"`{row['reason']}`", row["semantics"]] for row in missingness["reasons"]],
    )
    coverage_table = _table(
        ["Threshold", "Value"],
        [[key, f"{value:g}"] for key, value in missingness["coverage_thresholds"].items()],
    )
    not_applicable_rows = [
        [f"`{rule['parameter']}`", f"`{rule['bundle_fact']}`", f"`{rule['value']}`"]
        for rule in missingness["not_applicable_rules"]
    ] or [["\u2014", "\u2014", "\u2014"]]
    not_applicable_table = _table(["Parameter", "Bundle fact", "Value"], not_applicable_rows)
    return (
        f"### Reasons\n\n{reasons_table}\n\n"
        f"### Coverage thresholds\n\n{coverage_table}\n\n"
        f"### Not-applicable rules (D-MISS-5)\n\n{not_applicable_table}\n\n"
        f"OpenSSF no-dependents-signal proxy minimum: "
        f"{missingness['openssf_proxy_min_signals']} of 10 signals present."
    )


def _render_tie_break(document: dict[str, Any]) -> str:
    section = document["tie_break_levels"]
    rows = [
        [str(i + 1), level["field"], level["direction"], level["note"] or "\u2014"]
        for i, level in enumerate(section["levels"])
    ]
    table = _table(["Level", "Field", "Direction", "Note"], rows)
    return f"Composite is rounded to {section['composite_decimals']} decimal place(s) before comparison.\n\n{table}"


def _render_personas(document: dict[str, Any]) -> str:
    personas = document["personas"]
    pillar_names = [pillar["name"] for pillar in document["pillars"]]
    preset_rows = [
        [preset, *(f"{weights[name]:g}" for name in pillar_names)]
        for preset, weights in personas["presets"].items()
    ]
    presets_table = _table(["Persona", *pillar_names], preset_rows)
    gate_rows = [
        [
            gate["persona"],
            gate["description"],
            ", ".join(f"{key}={value:g}" for key, value in gate["thresholds"].items()) or "\u2014",
        ]
        for gate in personas["gates"]
    ]
    gates_table = _table(["Persona", "Description", "Thresholds"], gate_rows)
    return f"### Presets\n\n{presets_table}\n\n### Gates\n\n{gates_table}"


def _render_detectors(document: dict[str, Any]) -> str:
    detectors = document.get("detectors")
    if not detectors:
        return "_(no detector data in this document)_"
    rows = [
        [
            f"`{detector['name']}`",
            detector["trigger"],
            detector["direction"],
            ", ".join(
                f"{key}={value:g}" if isinstance(value, (int, float)) else f"{key}={value}"
                for key, value in detector["thresholds"].items()
            )
            or "\u2014",
        ]
        for detector in detectors
    ]
    return _table(["Name", "Trigger", "Direction", "Thresholds"], rows)


def _render_process_constants(document: dict[str, Any]) -> str:
    constants = document.get("not_hashed_process_constants")
    if not constants:
        return "_(no process-constant data in this document)_"
    rows = [
        [f"`{row['name']}`", f"`{row['module']}`", row["value"], row["gates"]] for row in constants
    ]
    return _table(["Name", "Module", "Value", "What it gates"], rows)


def _render_rfc_log(document: dict[str, Any]) -> str:
    rfc_log = document.get("rfc_log")
    if not rfc_log:
        return "_(no RFC log in this document)_"
    rows = []
    for row in reversed(rfc_log):  # newest first, matching CHANGELOG.md
        before = f"`{row['hash_before']}`" if row["hash_before"] else "*(none)*"
        after = f"`{row['hash_after']}`" if row["hash_after"] else "*(none)*"
        rows.append([row["date"], row["rfc"], row["summary"], f"{before} -> {after}"])
    return _table(["Date", "RFC", "Summary", "Hash before -> after"], rows)


def render_markdown(document: dict[str, Any]) -> str:
    """The full ``docs/METHODOLOGY-v2.md`` content for ``document`` (as
    returned by :func:`cvi.audit.methodology_history.methodology_with_history`
    or, for the sections that need it, :func:`cvi.core.methodology.methodology_document`).
    Pure function of ``document``: the same document always renders to the
    same markdown, byte for byte."""
    return f"""<!-- GENERATED FILE. Do not hand-edit.
Regenerate with: PYTHONPATH=$PWD .venv/bin/python scripts/render_methodology.py
Source: cvi.audit.methodology_history.methodology_with_history (`docs/v2/06-UX-DESIGN.md` section 3 F).
tests/test_render_methodology.py fails if this file drifts from a fresh render. -->

# CVI v2 Methodology

**Benchmark version:** `{document["benchmark_version"]}`
**Schema version:** `{document["schema_version"]}`
**Constants hash:** `{document["constants_hash"]}`

Every number below is read directly from `cvi.core.constants_v2` and the
`cvi.core.params` registry (or, for detectors and the two audit release
bars, their own frozen module) -- this page cannot drift from the code
that scores a repository, because it is rendered from the same values.

## Reproducing a snapshot

```
{REPRODUCE_COMMAND_TEMPLATE}
```

Every score this document describes is reproducible byte-for-byte from its
snapshot directory with the command above (D-VER-3): the same
`benchmark_version`, `constants_hash` and `code_commit` recorded in the
snapshot manifest are what a reader checks against this page.

## Pillars

{_render_pillars(document)}

## Parameters

{_render_parameters(document)}

## Gates

{_render_gates(document)}

## Missingness

{_render_missingness(document)}

## Tie-breakers

{_render_tie_break(document)}

## Personas

{_render_personas(document)}

## Detectors

{_render_detectors(document)}

## Not-hashed process constants

These three values gate whether a *snapshot* clears the bar for release,
not any per-repository score; they are process constants, not scoring
inputs, and are therefore not part of `CONSTANTS_HASH`
(`docs/v2/rfcs/README.md` "Process constants"). A change to any of them
still goes through the RFC procedure below.

{_render_process_constants(document)}

## Changing a constant: the RFC procedure

Every constant `cvi.core.constants_v2` defines is frozen (D-VER-1/2): a
change requires an RFC filed under `docs/v2/rfcs/`, a re-pinned
`CONSTANTS_HASH`, a `BENCHMARK_VERSION` bump and a named, independent
reviewer. The full procedure, its two documented enforcement limits, and
the pre-release version-bump rule live in
[`docs/v2/rfcs/README.md`](v2/rfcs/README.md); every change below followed it.

### RFC log

{_render_rfc_log(document)}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--changelog", type=Path, default=DEFAULT_CHANGELOG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if --out would change instead of writing it (CI drift check)",
    )
    args = parser.parse_args(argv)

    document = methodology_with_history(args.changelog)
    rendered = render_markdown(document)

    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else None
        if current != rendered:
            print(f"{args.out} is stale: rerun scripts/render_methodology.py", file=sys.stderr)
            return 1
        print(f"{args.out} is up to date")
        return 0

    args.out.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
