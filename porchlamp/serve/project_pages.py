"""One static page per ranked project, and the /check/ page (``scripts/export_static.py --stats``).

``/projects/<owner>/<name>/`` (lower case) answers "is <project> responsive to pull requests?" in its first
two sentences, for a reader or a crawler that does not run JavaScript. Nothing here is recomputed or typed
by hand: every value is read from the records the drawer shows (the API's ``/repos/{owner}/{name}`` and its
``/evidence``) and from the leaderboards; the labels, the tier note and the lens reasons are read from
``web/app/copy.js``, the single source the drawer uses (:func:`report_pages.copy_js_map`). The one thing written
here is the one-line meaning of each parameter (:data:`PARAM_MEANINGS`), pinned to the methodology's parameter
list by a test.

A value that was not measured is said plainly; a reason this code does not know stops the export
(:class:`report_pages.ReportError`) instead of printing a guess. ``scripts/verify_static_export.py`` recomputes
every number on every page from the API and compares.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from html import escape
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from porchlamp.serve.report_pages import (
    ECOSYSTEM_NAMES,
    ECOSYSTEM_SLUGS,
    GITHUB_REPO,
    SITE,
    WEB_APP,
    Facts,
    ReportError,
    _a,
    _t,
    copy_js_map,
    copy_js_string,
    fmt_int,
    long_date,
    page,
)

#: Similar projects shown on a page.
SIMILAR_COUNT = 5
#: The lens whose users the second answer sentence names.
FIRST_TIMER = "first_timer"
BALANCED = "balanced"
LENS_ORDER = ("balanced", "first_timer", "career_capital", "senior_systems", "ai_assisted")
#: ``owner`` and ``name`` as GitHub allows them, lower-cased: also safe as directory names.
_SEGMENT = re.compile(r"[a-z0-9._-]+")

#: One line on what each parameter measures. The names are copy.js's (``PARAM_NAMES``); these lines are the
#: only prose typed for the project pages. They follow the methodology's curve text and the engine's
#: parameter docstrings (``porchlamp/core/params``). A test pins the keys to the methodology's parameter list.
PARAM_MEANINGS = {
    "ttfhr_hours": "Median hours from an outside pull request opening to its first substantive human reply.",
    "dr90_decision_rate": "Share of outside pull requests that were merged or closed within 90 days.",
    "mr_first_rate": "Share of first-time outside authors' decided pull requests that were merged.",
    "gr14_ghost_rate": "Share of outside pull requests with no maintainer response, merge or close within 14 days.",
    "ttm_p90_hours": "Hours within which 90% of merged outside pull requests were merged.",
    "dependents_deduped": "Packages in the registry that depend on this project, counted once each.",
    "openssf_criticality": "OpenSSF criticality score from 0 to 1, computed from the project's public activity signals.",
    "downloads_monthly": "Downloads from the package registry in a month.",
    "legal_ip": "The contributor licence regime: DCO, none, a foundation CLA or a corporate CLA.",
    "elephant_factor": "The fewest organisations that account for half of the last year's commits.",
    "promotion_ladder": "How openly a contributor can become a maintainer, from closed to verified.",
    "pipeline_defenses": "How well the project's build and review pipeline defends against bad changes, 0 to 100.",
    "ai_posture": "Whether the project has a written policy on AI-assisted contributions.",
    "substantive_review_rate": "Share of decided pull requests that received a substantive review.",
    "newcomer_issue_supply": "Open, unassigned, up-to-date issues marked for newcomers.",
    "newcomer_conversion": "Share of first-time authors whose first pull request merged within 180 days.",
    "rr90_return_rate": "Share of those merged first-time authors who sent a second pull request within 90 days.",
    "dx": "Developer experience, 0 to 100: half CI run time, half setup signals.",
}


# --------------------------------------------------------------------------- #
# Addresses
# --------------------------------------------------------------------------- #
def project_path(owner: str, name: str) -> str:
    """``projects/<owner>/<name>/``, lower case. Raises when a segment is not a safe lower-case path segment."""
    parts = (owner.lower(), name.lower())
    for part in parts:
        if not _SEGMENT.fullmatch(part) or part in (".", ".."):
            raise ReportError(f"{owner}/{name} cannot be a page address: {part!r} is not a plain path segment")
    return f"projects/{parts[0]}/{parts[1]}/"


def project_url(owner: str, name: str) -> str:
    return f"{SITE}/{project_path(owner, name)}"


def check_no_collisions(keys: Sequence[tuple[str, str]]) -> None:
    """No two projects share an address once lower-cased (GitHub paths are case-sensitive on the host, names are not)."""
    seen: dict[str, tuple[str, str]] = {}
    for owner, name in keys:
        path = project_path(owner, name)
        if path in seen:
            other = seen[path]
            raise ReportError(f"{other[0]}/{other[1]} and {owner}/{name} share the address {path}; needs a ruling")
        seen[path] = (owner, name)


# --------------------------------------------------------------------------- #
# Formatting: the drawer's rules, in Python
# --------------------------------------------------------------------------- #
def js_fixed(value: float, places: int) -> str:
    """``value.toFixed(places)`` as JavaScript prints it: the double's exact value, rounded half up."""
    return str(Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def js_round(value: float) -> int:
    """``Math.round(value)``."""
    return math.floor(value + 0.5)


def percent_text(fraction: float) -> str:
    """A fraction as a whole percent, half up from its printed decimal (``0.6667`` -> ``67%``)."""
    return f"{(Decimal(repr(fraction)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)}%"


def coverage_percent(coverage: float) -> str:
    """``Math.round(coverage * 100)`` and a percent sign, as the drawer prints a coverage."""
    return f"{js_round(coverage * 100)}%"


def format_parameter_value(value: Any) -> str:
    """copy.js ``formatParameterValue``: integers as they are, 4 decimals at most, strings untouched."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            return "—"
        if number == 0:
            return "0"
        if number.is_integer():
            return str(int(number))
        if abs(number) < 0.0001:
            return format(Decimal(f"{number:.4g}"), "f")
        return format(Decimal(repr(js_round(number * 10000) / 10000)), "f")
    return str(value)


def reply_time_text(hours: float) -> str:
    """Hours under 48 (``6 hours``, ``1.5 hours``), else days with one decimal (``3.2 days``)."""
    if hours < 48:
        shown = Decimal(repr(hours)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        if shown == 0:
            return "under 0.1 hours"
        unit = "hour" if shown == 1 else "hours"
        return f"{shown.normalize():f} {unit}"
    shown = (Decimal(repr(hours)) / 24).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{shown.normalize():f} days"


def _day(text: str, what: str) -> date:
    try:
        return date.fromisoformat(text[:10])
    except (TypeError, ValueError) as error:
        raise ReportError(f"{what} {text!r} is not an ISO date") from error


def window_text(start: str, end: str) -> str:
    """``10 April to 7 October 2026``; both years when they differ."""
    first, last = _day(start, "window start"), _day(end, "window end")
    left = f"{first.day} {first:%B}" if first.year == last.year else long_date(first)
    return f"{left} to {long_date(last)}"


def _lowercase_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def _sentence_end(text: str) -> str:
    return text.rstrip(".") + "."


# --------------------------------------------------------------------------- #
# The copy the drawer uses
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Copy:
    """Everything the pages take from ``web/app/copy.js``."""

    pillar_names: Mapping[str, str]
    persona_names: Mapping[str, str]
    param_names: Mapping[str, str]
    unmeasured_reasons: Mapping[str, str]
    persona_gate_reasons: Mapping[str, str]
    insufficient_evidence: str
    coverage_bands: Mapping[str, str]
    tier_capped_explanation: str
    capped_suffix: str = " (capped)"

    @classmethod
    def load(cls, web_dir: Path = WEB_APP) -> "Copy":
        return cls(
            pillar_names=copy_js_map("PILLAR_NAMES", web_dir),
            persona_names=copy_js_map("PERSONA_NAMES", web_dir),
            param_names=copy_js_map("PARAM_NAMES", web_dir),
            unmeasured_reasons=copy_js_map("UNMEASURED_REASON_NAMES", web_dir),
            persona_gate_reasons=copy_js_map("PERSONA_GATE_COPY", web_dir),
            insufficient_evidence=copy_js_string("PERSONA_INSUFFICIENT_EVIDENCE_TEXT", web_dir),
            coverage_bands=copy_js_map("COVERAGE_BAND_LABELS", web_dir),
            tier_capped_explanation=copy_js_string("TIER_CAPPED_EXPLANATION", web_dir),
        )

    def tier_label(self, tier: str | None, ceiling: str | None) -> str:
        """copy.js ``tierLabel``."""
        if not tier:
            return "—"
        return f"{tier}{self.capped_suffix}" if ceiling and tier == ceiling else tier

    def coverage_band(self, coverage: float) -> str:
        """copy.js ``coverageBadgeText``: ``78% · Ceiling B``."""
        if coverage >= 0.95:
            band = "Full"
        elif coverage >= 0.8:
            band = self.coverage_bands["A"]
        elif coverage >= 0.6:
            band = self.coverage_bands["B"]
        else:
            band = "Below floor"
        return f"{coverage_percent(coverage)} · {band}"

    def missing_value(self, reason: str | None) -> str:
        """copy.js ``missingValueReason``."""
        if not reason:
            return "Not measured for this repository."
        return f"Not shown: {self.unmeasured_reasons.get(reason, reason)}"

    def persona_gate_reason(self, code: str) -> str:
        """copy.js ``personaGateReason`` for the codes a ranked project can carry; an unknown code stops the export."""
        if code.startswith("insufficient_evidence:"):
            return self.insufficient_evidence
        if code in self.persona_gate_reasons:
            return self.persona_gate_reasons[code]
        raise ReportError(f"persona gate code {code!r} has no wording in copy.js")


# --------------------------------------------------------------------------- #
# What the pages are built from
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Standing:
    """A project's place in one lens: a rank, or the plain reason it has none."""

    lens: str
    rank: int | None
    total: int
    reason: str | None


class ProjectIndex:
    """Ranks, lens standings and neighbours, from the exported leaderboards (the API's own rows)."""

    def __init__(self, boards: Mapping[str, Mapping[str, Any]], copy: Copy) -> None:
        missing = [lens for lens in LENS_ORDER if lens not in boards]
        if missing:
            raise ReportError(f"the leaderboards lack the lenses {missing}")
        self.copy = copy
        self.balanced = list(boards[BALANCED]["rows"])
        self.lens_rows = {lens: list(boards[lens]["rows"]) for lens in LENS_ORDER}
        self.totals = {lens: boards[lens]["total"] for lens in LENS_ORDER}
        for lens in LENS_ORDER:
            if len(boards[lens]["rows"]) != self.totals[lens]:
                raise ReportError(f"the {lens} leaderboard holds {len(boards[lens]['rows'])} rows, not {self.totals[lens]}")
        self.rank = {
            lens: {(r["owner"], r["name"]): r["rank"] for r in boards[lens]["rows"]} for lens in LENS_ORDER
        }
        check_no_collisions([(r["owner"], r["name"]) for r in self.balanced])
        self._position = {(r["owner"], r["name"]): i for i, r in enumerate(self.balanced)}
        by_eco: dict[str, list[int]] = {}
        for i, row in enumerate(self.balanced):
            by_eco.setdefault(row["ecosystem"], []).append(i)
        self._eco_positions = by_eco

    @property
    def keys(self) -> list[tuple[str, str]]:
        return [(r["owner"], r["name"]) for r in self.balanced]

    def standing(self, lens: str, detail: Mapping[str, Any]) -> Standing:
        key = (detail["owner"], detail["name"])
        rank = self.rank[lens].get(key)
        if rank is not None:
            return Standing(lens, rank, self.totals[lens], None)
        code = (detail.get("persona_gates") or {}).get(lens)
        if not code:
            raise ReportError(f"{key[0]}/{key[1]} is not ranked in {lens} and carries no reason")
        return Standing(lens, None, self.totals[lens], self.copy.persona_gate_reason(code))

    def similar(self, owner: str, name: str) -> list[dict]:
        """The five nearest ranked projects of the same ecosystem by balanced rank (ties go to the better rank)."""
        here = self._position[(owner, name)]
        positions = self._eco_positions[self.balanced[here]["ecosystem"]]
        near = sorted((p for p in positions if p != here), key=lambda p: (abs(p - here), p))[:SIMILAR_COUNT]
        return [self.balanced[p] for p in sorted(near)]


# --------------------------------------------------------------------------- #
# The answer
# --------------------------------------------------------------------------- #
def _param(evidence: Sequence[Mapping[str, Any]], param_id: str) -> Mapping[str, Any]:
    for row in evidence:
        if row["param_id"] == param_id:
            return row
    raise ReportError(f"the record has no evidence row for {param_id}")


def _measured(row: Mapping[str, Any], who: str) -> bool:
    """The drawer shows a value only when no unmeasured reason is set. Only a too-small sample is worded here."""
    reason = row["unmeasured_reason"]
    if reason is None:
        if row["value"] is None or not isinstance(row["n"], int):
            raise ReportError(f"{who}: {row['param_id']} is measured but has no value or count")
        return True
    if reason != "insufficient_n":
        raise ReportError(f"{who}: {row['param_id']} is unmeasured for {reason!r}; the answer sentence needs wording for it")
    return False


def _window(row: Mapping[str, Any], who: str) -> str:
    if not row["window_start"] or not row["window_end"]:
        raise ReportError(f"{who}: {row['param_id']} is measured but has no window")
    return window_text(row["window_start"], row["window_end"])


def _pull_requests(count: int) -> str:
    return f"{fmt_int(count)} pull request{'' if count == 1 else 's'}"


def _min_n(min_n: Mapping[str, Any], param_id: str) -> int:
    value = min_n.get(param_id)
    if not isinstance(value, int):
        raise ReportError(f"the methodology gives no minimum sample for {param_id}")
    return value


def answer_reply(
    detail: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], min_n: Mapping[str, Any]
) -> tuple[str, str]:
    """``(first sentence, the rest)`` of the reply answer: the median first human reply and the 14-day no-reply
    share, or, for a value with too small a sample, the counts and the minimum the methodology asks for (each
    parameter has its own sample, so the two counts can differ). The rest is empty when both are measured."""
    who = f"{detail['owner']}/{detail['name']}"
    ttfhr, ghost = _param(evidence, "ttfhr_hours"), _param(evidence, "gr14_ghost_rate")
    have_reply, have_ghost = _measured(ttfhr, who), _measured(ghost, who)
    min_reply, min_ghost = _min_n(min_n, "ttfhr_hours"), _min_n(min_n, "gr14_ghost_rate")
    if have_reply and have_ghost:
        return (
            f"Outside pull requests to {detail['name']} got a first human reply in a median of "
            f"{reply_time_text(ttfhr['value'])} ({_pull_requests(ttfhr['n'])}, {_window(ttfhr, who)}); "
            f"{percent_text(ghost['value'])} got none within 14 days.",
            "",
        )
    if have_reply:
        return (
            f"Outside pull requests to {detail['name']} got a first human reply in a median of "
            f"{reply_time_text(ttfhr['value'])} ({_pull_requests(ttfhr['n'])}, {_window(ttfhr, who)}).",
            "The share with no reply within 14 days has too small a sample to measure "
            f"({_pull_requests(ghost['n'])} counted; {min_ghost} needed).",
        )
    if have_ghost:
        noun = "pull request" if ghost["n"] == 1 else "pull requests"
        return (
            f"The reply time has too small a sample to measure ({_pull_requests(ttfhr['n'])} counted; {min_reply} needed).",
            f"{percent_text(ghost['value'])} of {fmt_int(ghost['n'])} outside {noun} got no reply within 14 days "
            f"({_window(ghost, who)}).",
        )
    needed = f"{min_reply} needed" if min_reply == min_ghost else f"{min_reply} and {min_ghost} needed"
    return (
        "The reply time and the share with no reply within 14 days both have too small a sample to measure "
        f"({fmt_int(ttfhr['n'])} and {_pull_requests(ghost['n'])} counted; {needed}).",
        "",
    )


def answer_second(standings: Mapping[str, Standing], copy: Copy) -> str:
    """Sentence 2: the overall rank and the first-timer rank, or the plain reason there is none."""
    overall, first = standings[BALANCED], standings[FIRST_TIMER]
    if overall.rank is None:
        raise ReportError("a project page needs a balanced rank")
    head = f"It ranks {fmt_int(overall.rank)} of {fmt_int(overall.total)} overall"
    if first.rank is not None:
        return f"{head} and {fmt_int(first.rank)} of {fmt_int(first.total)} for first-time contributors."
    return f"{head}. It is not ranked for first-time contributors: {_lowercase_first(_sentence_end(first.reason or ''))}"


def meta_description(sentence: str, limit: int = 160) -> str:
    """The first answer sentence (the first sentence only), under ``limit`` characters: whole; else without its bracket; else up to its
    ``;``; else cut at a word with an ellipsis."""
    if len(sentence) < limit:
        return sentence
    shorter = re.sub(r" \([^)]*\)(?=[;.])", "", sentence)
    if len(shorter) < limit:
        return shorter
    if ";" in shorter:
        clause = shorter.split(";", 1)[0] + "."
        if len(clause) < limit:
            return clause
    cut = shorter[: limit - 2].rsplit(" ", 1)[0].rstrip(" ;,:")
    return f"{cut}\u2026"


def project_title(owner: str, name: str) -> str:
    return f"{owner}/{name}: pull request response time and contributor experience | Porchlamp"


def issue_link(owner: str, name: str) -> str:
    return f"{GITHUB_REPO}/issues/new?title=" + quote(f"Porchlamp record: {owner}/{name}", safe="")


# --------------------------------------------------------------------------- #
# The page
# --------------------------------------------------------------------------- #
def _link(rel: str, row: Mapping[str, Any]) -> str:
    return f'<a href="{_a(rel + project_path(row["owner"], row["name"]))}">{_t(row["owner"])}/{_t(row["name"])}</a>'


def _parameter_rows(
    evidence: Sequence[Mapping[str, Any]], copy: Copy, order: Sequence[str]
) -> list[tuple[str, str, str, str, str, str]]:
    """``(param_id, name, value, count, window, meaning)`` per parameter, in the methodology's order."""
    by_id = {row["param_id"]: row for row in evidence}
    unknown = sorted(set(by_id) - set(order))
    if unknown:
        raise ReportError(f"evidence rows {unknown} are not in the methodology's parameter list")
    rows = []
    for param_id in order:
        row = by_id.get(param_id)
        if row is None:
            continue
        name = copy.param_names.get(param_id, param_id)
        meaning = PARAM_MEANINGS[param_id]
        if row["unmeasured_reason"]:
            rows.append((param_id, name, copy.missing_value(row["unmeasured_reason"]), "—", "—", meaning))
            continue
        count = fmt_int(row["n"]) if row["n"] else "—"
        window = window_text(row["window_start"], row["window_end"]) if row["window_start"] and row["window_end"] else "—"
        rows.append((param_id, name, format_parameter_value(row["value"]), count, window, meaning))
    return rows


def render_project(
    f: Facts,
    *,
    detail: Mapping[str, Any],
    evidence: Sequence[Mapping[str, Any]],
    index: ProjectIndex,
    copy: Copy,
    param_order: Sequence[str],
    min_n: Mapping[str, Any],
    bucket: str,
    as_of: date,
) -> str:
    """One project's page, from its API record and evidence."""
    owner, name = detail["owner"], detail["name"]
    canonical = project_url(owner, name)
    rel = "../../../"
    standings = {lens: index.standing(lens, detail) for lens in LENS_ORDER}
    first, rest = answer_reply(detail, evidence, min_n)
    answer = " ".join(part for part in (first, rest, answer_second(standings, copy)) if part)
    title, description = project_title(owner, name), meta_description(first)

    tier = copy.tier_label(detail["tier"], detail["tier_ceiling"])
    capped = bool(detail["tier_ceiling"]) and detail["tier"] == detail["tier_ceiling"]
    capped_note = f'<p class="note">{_t(copy.tier_capped_explanation)}</p>' if capped else ""
    ecosystem = ECOSYSTEM_NAMES[detail["ecosystem"]]
    composite = (
        f"<li>Score: {js_fixed(detail['porchlamp'], 2)}</li>"
        f"<li>Coverage: {_t(copy.coverage_band(detail['coverage']))}</li>"
        f"<li>Tier: {_t(tier)}</li>"
        f"<li>Quadrant: {_t(detail['quadrant'])}</li>"
        f'<li>Ecosystem: <a href="{rel}{ECOSYSTEM_SLUGS[detail["ecosystem"]]}/">{_t(ecosystem)}</a></li>'
    )
    pillar_rows = "".join(
        f'<tr><th scope="row">{_t(label)}</th><td>{js_fixed(detail[f"p_{pillar}"], 1) if detail[f"p_{pillar}"] is not None else "&mdash;"}</td>'
        f'<td>{coverage_percent(detail[f"c_{pillar}"] or 0)}</td></tr>'
        for pillar, label in copy.pillar_names.items()
    )
    lens_rows = ""
    for lens in LENS_ORDER:
        s = standings[lens]
        status = f"Rank {fmt_int(s.rank)} of {fmt_int(s.total)}" if s.rank is not None else f"Not ranked: {s.reason}"
        lens_rows += f'<tr><th scope="row">{_t(copy.persona_names[lens])}</th><td>{_t(status)}</td></tr>'
    param_rows = "".join(
        f'<tr><th scope="row" title="{_a(pid)}">{_t(label)}</th><td data-label="Value">{_t(value)}</td>'
        f'<td data-label="Count">{_t(count)}</td><td data-label="Window">{_t(window)}</td>'
        f'<td data-label="Meaning">{_t(meaning)}</td></tr>'
        for pid, label, value, count, window, meaning in _parameter_rows(evidence, copy, param_order)
    )
    similar = index.similar(owner, name)
    similar_html = (
        "<ul>" + "".join(f"<li>{_link(rel, row)} (rank {fmt_int(row['rank'])})</li>" for row in similar) + "</ul>"
        if similar
        else "<p>No other ranked project in this ecosystem.</p>"
    )
    record_file = f"data/{f.snapshot_id}/repos/{bucket}.json"
    body = f"""      <article class="seo-fallback report project">
        <h1>{_t(owner)}/{_t(name)}</h1>
        <p class="lede">What happens to a stranger's pull request here, in the Porchlamp snapshot of {_t(long_date(f.snapshot_date))}.</p>
        <p class="answer" id="answer">{_t(answer)}</p>
        <h2 id="rating">Rating</h2>
        <ul class="facts">{composite}</ul>
        {capped_note}
        <h2 id="pillars">Five pillars</h2>
        <div class="seo-fallback-table-wrap"><table><caption>Score out of 100, and how much of the pillar was measured</caption><thead><tr><th scope="col">Pillar</th><th scope="col">Score</th><th scope="col">Coverage</th></tr></thead><tbody>{pillar_rows}</tbody></table></div>
        <h2 id="lenses">Rank in each lens</h2>
        <div class="seo-fallback-table-wrap"><table><thead><tr><th scope="col">Lens</th><th scope="col">Standing</th></tr></thead><tbody>{lens_rows}</tbody></table></div>
        <details><summary>The measured parameters</summary>
        <div class="seo-fallback-table-wrap"><table class="wrap-cells"><thead><tr><th scope="col">Parameter</th><th scope="col">Value</th><th scope="col">Count</th><th scope="col">Window</th><th scope="col">What it measures</th></tr></thead><tbody>{param_rows}</tbody></table></div>
        </details>
        <h2 id="similar">Similar projects</h2>
        <p>The ranked projects nearest this one in the same ecosystem.</p>
        {similar_html}
        <h2 id="evidence">Evidence</h2>
        <ul class="evidence">
          <li>Snapshot {_t(f.snapshot_id)}, data as of {_t(long_date(as_of))}.</li>
          <li>Record hash: <code>{_t(detail['raw_hash'])}</code></li>
          <li>The full record is in <a href="{_a(rel + record_file)}">{_t(record_file)}</a>, under the key <code>{_t(owner)}/{_t(name)}</code>.</li>
          <li>How every number is measured: <a href="{rel}methodology/">Porchlamp methodology</a>.</li>
        </ul>
        <h2 id="correction">Is something wrong?</h2>
        <p><a href="{_a(issue_link(owner, name))}">Open an issue about this record</a> and say what looks wrong.</p>
      </article>"""
    ld = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Porchlamp", "item": f"{SITE}/"},
            {"@type": "ListItem", "position": 2, "name": f"{owner}/{name}", "item": canonical},
        ],
    }
    # The page's address is also the project's side panel in the app: web/app/back-forward.js lets Back and Forward
    # between the app's entries load the app again (the page's own script).
    return page(
        title=title, description=description, canonical=canonical, body=body, ld=ld, scripts=("/back-forward.js",),
    )


# --------------------------------------------------------------------------- #
# /check/ and the sitemap
# --------------------------------------------------------------------------- #
CHECK_PATTERN_TEXT = "To check a project, open https://porchlamp.org/projects/<owner>/<name>/ (lower case)."
CHECK_TITLE = "Check an open-source project: how it treats outside pull requests | Porchlamp"
CHECK_DESCRIPTION = (
    "Search the open-source projects Porchlamp ranks and open the page that says how fast they reply to a "
    "stranger's pull request."
)


def check_index(index: ProjectIndex) -> list[str]:
    """The search index: ``owner/name`` in the real spelling, in balanced rank order. The address follows the pattern."""
    return [f"{owner}/{name}" for owner, name in index.keys]


def render_check(f: Facts, *, ranked: int, example: Mapping[str, Any]) -> str:
    """The /check/ page: a search box over ``projects.json`` (loaded by ``check.js``) and the address pattern."""
    canonical, rel = f"{SITE}/check/", "../"
    body = f"""      <article class="seo-fallback report check">
        <h1>Check an open-source project</h1>
        <p class="lede">Find out what happens to a stranger's pull request at a project, in the Porchlamp snapshot of {_t(long_date(f.snapshot_date))}.</p>
        <p>{_t(CHECK_PATTERN_TEXT)} For example, <a href="{_a(rel + project_path(example['owner'], example['name']))}">{_t(example['owner'])}/{_t(example['name'])}</a>.</p>
        <div class="check-form" role="search">
          <label for="check-q">Search by owner or project name</label>
          <input id="check-q" type="search" autocomplete="off" spellcheck="false" placeholder="owner/name" data-check-input>
        </div>
        <ul class="check-results" id="check-results" aria-live="polite" data-check-results></ul>
        <noscript><p>Search needs JavaScript. Without it, open the address above, or browse the <a href="{rel}">rankings</a>.</p></noscript>
        <p class="note">Only the {_t(fmt_int(ranked))} ranked projects have a page. A project that is not ranked, and the reason, is listed under <a href="{rel}not-ranked/">Not ranked</a>.</p>
      </article>"""
    ld = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Porchlamp", "item": f"{SITE}/"},
            {"@type": "ListItem", "position": 2, "name": "Check a project", "item": canonical},
        ],
    }
    return page(title=CHECK_TITLE, description=CHECK_DESCRIPTION, canonical=canonical, body=body, ld=ld, scripts=("/check.js",))


def sitemap_projects(index: ProjectIndex, as_of: date) -> str:
    """``sitemap-projects.xml``: every ranked project's page in balanced-rank order, each with the snapshot's ``as_of`` date.

    One file holds them all: a sitemap may list 50,000 addresses."""
    stamp = as_of.isoformat()
    urls = "".join(
        f"  <url>\n    <loc>{escape(project_url(owner, name), quote=False)}</loc>\n    <lastmod>{stamp}</lastmod>\n  </url>\n"
        for owner, name in index.keys
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + urls + "</urlset>\n"
    )


def dumps_json(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
