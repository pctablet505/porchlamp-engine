"""What a ranked project's page says first, and its head (``scripts/export_static.py``).

``/projects/<owner>/<name>/`` (lower case) answers "is <project> responsive to pull requests?" in its first
sentences, for a reader or a crawler that does not run JavaScript. The page is drawn by the app's own view
(``web/app/views/project.js``) at export (``web/prerender.mjs``), from the project's record in
``data/<sid>/repos/<bucket>.json``. This module writes what the view only prints: the record's answer sentences, its
place in each lens and its similar projects (:func:`record_extras`, from the leaderboards), and the page's head
(:func:`project_page`). The lens reasons are read from ``web/app/copy.js``, the single source the app uses
(:func:`report_pages.copy_js_map`).

A project that was published and is no longer ranked keeps its address: its page says it is not ranked, and why
(:func:`delisted_reason`, :func:`delisted_description`, :func:`project_page` with the title of :func:`delisted_title`).

A value that was not measured is said plainly; a reason this code does not know stops the export
(:class:`report_pages.ReportError`) instead of printing a guess. ``scripts/verify_static_export.py`` recomputes
every sentence and number on every page from the API and compares.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from decimal import ROUND_HALF_UP, Decimal
from html import escape
from pathlib import Path
from typing import Any, Mapping, Sequence

from porchlamp.serve.report_pages import (
    PAGE_SCRIPTS,
    SITE,
    WEB_APP,
    ReportError,
    _article_head,
    _with_ld,
    app_page,
    copy_js_map,
    copy_js_string,
    fmt_int,
    long_date,
)

#: Similar projects shown on a page.
SIMILAR_COUNT = 5
#: The lens whose users the second answer sentence names.
FIRST_TIMER = "first_timer"
BALANCED = "balanced"
LENS_ORDER = ("balanced", "first_timer", "career_capital", "senior_systems", "ai_assisted")
#: ``owner`` and ``name`` as GitHub allows them, lower-cased: also safe as directory names.
_SEGMENT = re.compile(r"[a-z0-9._-]+")

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
# Formatting
# --------------------------------------------------------------------------- #
def js_fixed(value: float, places: int) -> str:
    """``value.toFixed(places)`` as JavaScript prints it: the double's exact value, rounded half up."""
    return str(Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def percent_text(fraction: float) -> str:
    """A fraction as a whole percent, half up from its printed decimal (``0.6667`` -> ``67%``)."""
    return f"{(Decimal(repr(fraction)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)}%"


def rank_band(rank: int, total: int) -> str:
    """Where a rank sits among ``total``: ``the top N%`` in the upper half, else ``the bottom N%``. N is rounded up, so
    the claim holds for every rank (1 of 2,132 is in the top 1%, never "the top 0%"; 2,039 of 2,132, the 94th from
    the bottom, is in the bottom 5%, not the bottom 4%)."""
    if rank * 2 <= total:
        return f"the top {-(-rank * 100 // total)}%"
    return f"the bottom {-(-(total - rank + 1) * 100 // total)}%"


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
# The copy the app uses
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Copy:
    """What the lens reasons and the home's generated texts take from ``web/app/copy.js``."""

    pillar_names: Mapping[str, str]
    persona_gate_reasons: Mapping[str, str]
    insufficient_evidence: str
    capped_suffix: str = " (capped)"

    @classmethod
    def load(cls, web_dir: Path = WEB_APP) -> "Copy":
        return cls(
            pillar_names=copy_js_map("PILLAR_NAMES", web_dir),
            persona_gate_reasons=copy_js_map("PERSONA_GATE_COPY", web_dir),
            insufficient_evidence=copy_js_string("PERSONA_INSUFFICIENT_EVIDENCE_TEXT", web_dir),
        )

    def tier_label(self, tier: str | None, ceiling: str | None) -> str:
        """copy.js ``tierLabel``."""
        if not tier:
            return "—"
        return f"{tier}{self.capped_suffix}" if ceiling and tier == ceiling else tier

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


def _state(row: Mapping[str, Any], who: str) -> str:
    """``"measured"``, ``"small"`` (a too-small sample) or ``"incomplete"`` (the collector does not hold the project's
    whole PR record for the window, RFC-0013): the drawer shows a value only when no unmeasured reason is set, and
    only these two reasons are worded here."""
    reason = row["unmeasured_reason"]
    if reason is None:
        if row["value"] is None or not isinstance(row["n"], int):
            raise ReportError(f"{who}: {row['param_id']} is measured but has no value or count")
        return "measured"
    if reason == "insufficient_n":
        return "small"
    if reason == "collector_failure":
        return "incomplete"
    raise ReportError(f"{who}: {row['param_id']} is unmeasured for {reason!r}; the answer sentence needs wording for it")


#: RFC-0013: why a reply value is not measured when the PR record does not cover the window.
_INCOMPLETE_RECORD = "we do not hold a complete record of its pull requests for the window"


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


@dataclass(frozen=True)
class Reply:
    """The reply answer: its first sentence, which names the project; the rest (empty when both values are measured);
    and the first sentence as the head's description says it, after the project's name and rank (:func:`head_description`):
    without the name and without the bracket of counts and dates."""

    first: str
    rest: str
    short: str


def answer_reply(detail: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], min_n: Mapping[str, Any]) -> Reply:
    """The median first human reply and the 14-day no-reply share, or, for a value with too small a sample, the counts
    and the minimum the methodology asks for (each parameter has its own sample, so the two counts can differ), or,
    for a value the collector could not measure because the PR record does not cover the window, that reason."""
    who = f"{detail['owner']}/{detail['name']}"
    ttfhr, ghost = _param(evidence, "ttfhr_hours"), _param(evidence, "gr14_ghost_rate")
    reply_state, ghost_state = _state(ttfhr, who), _state(ghost, who)
    min_reply, min_ghost = _min_n(min_n, "ttfhr_hours"), _min_n(min_n, "gr14_ghost_rate")

    def ghost_unmeasured() -> str:  # a count is read only for a too-small sample: an incomplete record has none
        if ghost_state == "small":
            return ("The share with no reply within 14 days has too small a sample to measure "
                    f"({_pull_requests(ghost['n'])} counted; {min_ghost} needed).")
        return f"The share with no reply within 14 days is not measured: {_INCOMPLETE_RECORD}."

    if reply_state == "measured":
        median = f"got a first human reply in a median of {reply_time_text(ttfhr['value'])}"
        bracket = f" ({_pull_requests(ttfhr['n'])}, {_window(ttfhr, who)})"
        if ghost_state == "measured":
            tail = f"; {percent_text(ghost['value'])} got none within 14 days."
            return Reply(f"Outside pull requests to {who} {median}{bracket}{tail}", "", f"Outside pull requests {median}{tail}")
        return Reply(
            f"Outside pull requests to {who} {median}{bracket}.",
            ghost_unmeasured(),
            f"Outside pull requests {median}.",
        )
    if reply_state == ghost_state == "small":
        needed = f"{min_reply} needed" if min_reply == min_ghost else f"{min_reply} and {min_ghost} needed"
        both = "the reply time and the share with no reply within 14 days both have too small a sample to measure"
        return Reply(
            f"For {who}, {both} ({fmt_int(ttfhr['n'])} and {_pull_requests(ghost['n'])} counted; {needed}).",
            "",
            f"{both[:1].upper()}{both[1:]}.",
        )
    if reply_state == ghost_state == "incomplete":
        both = "the reply time and the share with no reply within 14 days are not measured"
        return Reply(f"For {who}, {both}: {_INCOMPLETE_RECORD}.", "", f"{both[:1].upper()}{both[1:]}.")
    if reply_state == "small":
        first = (f"For {who}, the reply time has too small a sample to measure ({_pull_requests(ttfhr['n'])} counted; "
                 f"{min_reply} needed).")
        short = "The reply time has too small a sample to measure."
    else:
        first = f"For {who}, the reply time is not measured: {_INCOMPLETE_RECORD}."
        short = "The reply time is not measured."
    if ghost_state == "measured":
        noun = "pull request" if ghost["n"] == 1 else "pull requests"
        rest = (f"{percent_text(ghost['value'])} of {fmt_int(ghost['n'])} outside {noun} got no reply within 14 days "
                f"({_window(ghost, who)}).")
    else:
        rest = ghost_unmeasured()
    return Reply(first, rest, short)


def rank_sentence(overall: Standing) -> str:
    """The overall rank: ``It ranks 3 of 4,322 overall.``"""
    if overall.rank is None:
        raise ReportError("a project page needs a balanced rank")
    return f"It ranks {fmt_int(overall.rank)} of {fmt_int(overall.total)} overall."


def first_timer_sentence(first: Standing) -> str:
    """The first-timer rank and where it sits (:func:`rank_band`), or the plain reason there is none."""
    if first.rank is not None:
        return (
            f"For first-time contributors it ranks {fmt_int(first.rank)} of {fmt_int(first.total)} "
            f"({rank_band(first.rank, first.total)})."
        )
    return f"It is not ranked for first-time contributors: {_lowercase_first(_sentence_end(first.reason or ''))}"


#: The longest head description: what a search result shows of it without cutting.
DESCRIPTION_LIMIT = 155


def head_description(who: str, overall: Standing, reply: Reply, limit: int = DESCRIPTION_LIMIT) -> str:
    """The page's description: the project, its rank, and its reply answer (``keras-team/keras ranks 2,531 of 4,322.
    Outside pull requests got a first human reply in a median of 6.2 days; 34% got none within 14 days.``), at most
    ``limit`` characters: whole; else up to the reply's ``;``; else cut at a word with an ellipsis. The name and the
    rank are never cut."""
    if overall.rank is None:
        raise ReportError("a project page needs a balanced rank")
    lead = f"{who} ranks {fmt_int(overall.rank)} of {fmt_int(overall.total)}."
    whole = f"{lead} {reply.short}"
    if len(whole) <= limit:
        return whole
    if ";" in reply.short:
        clause = f"{lead} {reply.short.split(';', 1)[0]}."
        if len(clause) <= limit:
            return clause
    if len(lead) + 2 > limit:
        raise ReportError(f"{who}: the name and rank alone are longer than {limit} characters; the description needs a ruling")
    cut = whole[: limit - 1].rsplit(" ", 1)[0].rstrip(" ;,:.")
    return f"{cut}\u2026" if len(cut) > len(lead) else lead


def record_extras(
    index: ProjectIndex, detail: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]], min_n: Mapping[str, Any]
) -> dict | None:
    """What a ranked project's record carries beside the API's ``detail`` and ``evidence``, for the view to print: the
    answer sentences, its standing in each lens and its similar projects. None for a project without a balanced rank
    (it has no page)."""
    owner, name = detail["owner"], detail["name"]
    if (owner, name) not in index.rank[BALANCED]:
        return None
    standings = {lens: index.standing(lens, detail) for lens in LENS_ORDER}
    reply = answer_reply(detail, evidence, min_n)
    answer = [part for part in (reply.first, reply.rest) if part]
    answer += [rank_sentence(standings[BALANCED]), first_timer_sentence(standings[FIRST_TIMER])]
    return {
        "answer": answer,
        "standings": {lens: {"rank": s.rank, "total": s.total, "reason": s.reason} for lens, s in standings.items()},
        "similar": [{"owner": r["owner"], "name": r["name"], "rank": r["rank"]} for r in index.similar(owner, name)],
    }


# --------------------------------------------------------------------------- #
# The page
# --------------------------------------------------------------------------- #
def project_title(owner: str, name: str) -> str:
    return f"{owner}/{name}: pull request response time and contributor experience | Porchlamp"


def delisted_title(owner: str, name: str, snapshot_day: date) -> str:
    return f"{owner}/{name}: not ranked in the Porchlamp snapshot of {long_date(snapshot_day)} | Porchlamp"


@lru_cache(maxsize=1)
def _reason_sentences() -> dict[str, str]:
    """The sentence of each reason a project is not ranked for: copy.js's gate reasons and unrated reasons, the ones the
    side panel's banner reads (``gateReasonText``)."""
    return {**copy_js_map("UNRATED_REASON_COPY"), **copy_js_map("GATE_REASON_COPY")}


def delisted_reason(detail: Mapping[str, Any]) -> str:
    """The code a record is not ranked for: its gate reason or, without one, its unrated reason, without the engine's
    ``"unrated: "`` prefix (copy.js ``bareUnratedReason``). A record that is not ranked and has none stops the export."""
    code = (detail.get("gate_reason") or detail.get("unrated_reason") or "").removeprefix("unrated: ")
    if not code:
        raise ReportError(f"{detail['owner']}/{detail['name']} is not ranked and carries no reason; needs a ruling")
    return code


def delisted_reason_text(code: str) -> str:
    """copy.js's sentence for a reason code; a code it has no wording for stops the export instead of printing a guess."""
    sentences = _reason_sentences()
    if code not in sentences:
        raise ReportError(f"the reason {code!r} has no wording in copy.js (GATE_REASON_COPY, UNRATED_REASON_COPY)")
    return sentences[code]


def delisted_description(who: str, reason_text: str, limit: int = DESCRIPTION_LIMIT) -> str:
    """The head's description of a project that is not ranked: its name, then its reason's sentence (``tencent/ncnn is not
    ranked: more than half of outside pull requests got no human reply, review or merge on GitHub within 14 days.``), at
    most ``limit`` characters: whole, else cut at a word with an ellipsis. The name is never cut."""
    lead = f"{who} is not ranked: "
    whole = lead + _lowercase_first(_sentence_end(reason_text))
    if len(whole) <= limit:
        return whole
    if len(lead) + 1 > limit:
        raise ReportError(f"{who}: the name alone is longer than {limit} characters; the description needs a ruling")
    cut = whole[: limit - 1].rsplit(" ", 1)[0].rstrip(" ;,:.")
    return f"{cut}\u2026" if len(cut) > len(lead.rstrip()) else lead.rstrip(" :")


def project_page(owner: str, name: str, description: str, title: str | None = None) -> str:
    """A project's page (``title`` is the default one of a ranked project) for ``web/prerender.mjs`` to draw the header, nav, footer and the project view into:
    the app's page with the project's head (title, description, canonical address, link previews, breadcrumb). The
    page's own script (``back-forward.js``) loads the app again when Back or Forward returns to one of its entries:
    the page's address is also the project's side panel in the app."""
    title, canonical = title or project_title(owner, name), project_url(owner, name)
    built = app_page(
        title=title, description=description, head=_article_head(title=title, description=description, canonical=canonical),
        scripts=PAGE_SCRIPTS["project"][1:],
    )
    ld = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Porchlamp", "item": f"{SITE}/"},
            {"@type": "ListItem", "position": 2, "name": f"{owner}/{name}", "item": canonical},
        ],
    }
    return _with_ld(built, ld)


# --------------------------------------------------------------------------- #
# The sitemap
# --------------------------------------------------------------------------- #
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
