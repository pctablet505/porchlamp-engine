"""Porchlamp demo application: deterministic fixture data + the web front end.

This is the full logic that used to live at `scripts/demo_app.py`, folded into
a test helper module. `scripts/demo_app.py` is now a one-release deprecation shim
re-exporting these names so `scripts/demo_app.py` and `tests/test_webapp.py`
both keep working unchanged.

Seeds 26 fictional, fully deterministic repositories (six ecosystems, all
four quadrants, one gated black-hole repo, two off-platform repos, stars
spread 10..80000) into an ``OperationalStore`` — in-memory by default,
``--db PATH`` for a file-backed store — then serves the Porchlamp REST API
(:func:`porchlamp.api.create_app`) plus the offline single-page UI in
``web/index.html`` mounted at ``/``.

Pillar scores are pinned per repo through the documented
``porchlamp.engine.score_repository`` ``pillar_scorers`` DI seam, so the stage-0
gates, the Phase-4 decomposition, and the Phase-5 balanced projection all
run for real while the five pillar inputs stay exact and reproducible.
No randomness anywhere: the same rows every run (``last_indexed_at`` is
pinned to :data:`DEMO_NOW` as well).

Run:
    .venv/bin/python scripts/demo_app.py              # in-memory, port 8000
    PORT=8765 .venv/bin/python scripts/demo_app.py    # port override
    .venv/bin/python scripts/demo_app.py --db /tmp/porchlamp-demo.sqlite
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from porchlamp.api import create_app
from porchlamp.deepdive import build_deep_dive
from porchlamp.engine import PILLAR_NAMES, score_repository
from porchlamp.lakehouse import OperationalStore, apply_raw_metrics
from porchlamp.models import (
    AIPosture,
    AuthorAssociation,
    Ecosystem,
    LegalIPModel,
    Persona,
    RawPREvent,
    RepoMeta,
)
from tests.fixtures.synthetic_catalog import build_benchmark_store

#: Fixed "now" for the demo: keeps ghost-rate math and last_indexed_at
#: deterministic across runs.
DEMO_NOW: datetime = datetime(2026, 9, 11, 12, 0, 0)

_REPO_ROOT: Path = Path(__file__).resolve().parent.parent.parent
_WEB_DIR: Path = _REPO_ROOT / "web"

# --------------------------------------------------------------------------- #
# Fixture data
# --------------------------------------------------------------------------- #
# 26 deterministic repos. Each entry pins the five pillar scores (P1
# responsiveness, P2 criticality, P3 governance, P4 rigor, P5 accessibility)
# plus the raw metrics hydrated via porchlamp.lakehouse.apply_raw_metrics.
#
# Quadrant spread (boundary 50, >= counts as high):
#   Sweet Spot  (10): high leverage, low friction   — e.g. atlas-core (91.5, 11.9)
#   Crucible    (4):  high leverage, high friction  — e.g. monolith-db (91.5, 64.5)
#   Nursery     (6):  low leverage, low friction    — e.g. seed-utils (20.5, 35.0)
#   Graveyard   (6):  low leverage, high friction   — incl. the gated repo
# Ecosystems: pypi 7, npm 6, crates 4, go 4, maven 3, systems 2.
# Stars: 10..80000 so min_stars=100 (the API default) drops 4 repos and
# min_stars=0 (the demo page default) shows all of them.
_FIXTURES: tuple[dict[str, Any], ...] = (
    # ---- Sweet Spot (high leverage / low friction) ---------------------- #
    {
        "owner": "helios", "name": "atlas-core", "ecosystem": Ecosystem.PYPI,
        "pillars": (90, 95, 85, 90, 88),
        "stars": 80000, "forks": 5200, "downloads": 9_500_000, "dependents": 42_000,
        "ef": 4, "legal": LegalIPModel.FOUNDATION_CLA, "ai": AIPosture.REGULATED,
        "off_platform": False, "commits": 320,
    },
    {
        "owner": "helios", "name": "atlas-ui", "ecosystem": Ecosystem.NPM,
        "pillars": (85, 90, 90, 80, 82),
        "stars": 42000, "forks": 3100, "downloads": 7_800_000, "dependents": 18_500,
        "ef": 4, "legal": LegalIPModel.FOUNDATION_CLA, "ai": AIPosture.REGULATED,
        "off_platform": False, "commits": 260,
    },
    {
        "owner": "forge", "name": "data-mill", "ecosystem": Ecosystem.PYPI,
        "pillars": (88, 80, 78, 85, 80),
        "stars": 15400, "forks": 980, "downloads": 2_100_000, "dependents": 9_800,
        "ef": 3, "legal": LegalIPModel.FOUNDATION_CLA, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 190,
    },
    {
        "owner": "ember", "name": "ember-cli", "ecosystem": Ecosystem.CRATES,
        "pillars": (80, 88, 75, 70, 78),
        "stars": 9800, "forks": 610, "downloads": 1_400_000, "dependents": 4_200,
        "ef": 3, "legal": LegalIPModel.DCO, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 150,
    },
    {
        "owner": "lattice", "name": "lattice", "ecosystem": Ecosystem.GO,
        "pillars": (92, 84, 82, 78, 90),
        "stars": 30500, "forks": 1900, "downloads": 5_600_000, "dependents": 12_400,
        "ef": 5, "legal": LegalIPModel.FOUNDATION_CLA, "ai": AIPosture.REGULATED,
        "off_platform": False, "commits": 400,
    },
    {
        "owner": "quick", "name": "quickpack", "ecosystem": Ecosystem.NPM,
        "pillars": (75, 70, 88, 65, 74),
        "stars": 4700, "forks": 280, "downloads": 620_000, "dependents": 2_100,
        "ef": 2, "legal": LegalIPModel.DCO, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 95,
    },
    {
        "owner": "forge", "name": "pipelint", "ecosystem": Ecosystem.PYPI,
        "pillars": (82, 76, 70, 88, 68),
        "stars": 2600, "forks": 140, "downloads": 410_000, "dependents": 1_300,
        "ef": 2, "legal": LegalIPModel.DCO, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 80,
    },
    {
        "owner": "stratos", "name": "stratos", "ecosystem": Ecosystem.MAVEN,
        "pillars": (78, 88, 84, 80, 72),
        "stars": 12800, "forks": 720, "downloads": 1_900_000, "dependents": 6_500,
        "ef": 4, "legal": LegalIPModel.CORP_CLA_RECIPROCAL, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 170,
    },
    # Off-platform repo #1: mailing-list review workflow (gate 3 routing, not
    # a failure) — a PROSCRIBED-AI systems project.
    {
        "owner": "corelib", "name": "corelib", "ecosystem": Ecosystem.SYSTEMS,
        "pillars": (86, 92, 95, 90, 84),
        "stars": 6600, "forks": 430, "downloads": 880_000, "dependents": 3_900,
        "ef": 5, "legal": LegalIPModel.FOUNDATION_CLA, "ai": AIPosture.PROSCRIBED,
        "off_platform": True, "commits": 210,
    },
    # ---- Crucible (high leverage / high friction) ------------------------ #
    {
        "owner": "monolith", "name": "monolith-db", "ecosystem": Ecosystem.PYPI,
        "pillars": (15, 94, 80, 95, 20),
        "stars": 24000, "forks": 1600, "downloads": 4_700_000, "dependents": 28_000,
        "ef": 5, "legal": LegalIPModel.FOUNDATION_CLA, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 240,
    },
    {
        "owner": "heavy", "name": "heavy-runtime", "ecosystem": Ecosystem.NPM,
        "pillars": (20, 92, 78, 88, 25),
        "stars": 18500, "forks": 1100, "downloads": 3_300_000, "dependents": 15_600,
        "ef": 4, "legal": LegalIPModel.FOUNDATION_CLA, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 205,
    },
    {
        "owner": "ember", "name": "kernel-adj", "ecosystem": Ecosystem.CRATES,
        "pillars": (30, 88, 72, 90, 18),
        "stars": 3400, "forks": 260, "downloads": 540_000, "dependents": 1_800,
        "ef": 3, "legal": LegalIPModel.DCO, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 70,
    },
    {
        "owner": "legacy", "name": "legacy-net", "ecosystem": Ecosystem.GO,
        "pillars": (25, 90, 68, 84, 30),
        "stars": 7700, "forks": 480, "downloads": 960_000, "dependents": 4_400,
        "ef": 3, "legal": LegalIPModel.DCO, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 110,
    },
    # ---- Nursery (low leverage / low friction) --------------------------- #
    {
        "owner": "seed", "name": "seed-utils", "ecosystem": Ecosystem.PYPI,
        "pillars": (85, 20, 15, 25, 90),
        "stars": 45, "forks": 6, "downloads": 18_000, "dependents": 40,
        "ef": 1, "legal": LegalIPModel.DCO, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 9,
    },
    {
        "owner": "seed", "name": "tiny-hook", "ecosystem": Ecosystem.NPM,
        "pillars": (90, 25, 20, 30, 85),
        "stars": 10, "forks": 2, "downloads": 9_000, "dependents": 12,
        "ef": 1, "legal": LegalIPModel.DCO, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 5,
    },
    {
        "owner": "proto", "name": "proto-gen", "ecosystem": Ecosystem.CRATES,
        "pillars": (78, 35, 28, 38, 72),
        "stars": 260, "forks": 18, "downloads": 60_000, "dependents": 150,
        "ef": 2, "legal": LegalIPModel.DCO, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 25,
    },
    {
        "owner": "start", "name": "starter-kit", "ecosystem": Ecosystem.GO,
        "pillars": (82, 30, 22, 42, 76),
        "stars": 830, "forks": 54, "downloads": 130_000, "dependents": 320,
        "ef": 2, "legal": LegalIPModel.DCO, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 34,
    },
    {
        "owner": "micro", "name": "micro-css", "ecosystem": Ecosystem.NPM,
        "pillars": (88, 18, 30, 22, 92),
        "stars": 1300, "forks": 88, "downloads": 240_000, "dependents": 510,
        "ef": 1, "legal": LegalIPModel.DCO, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 28,
    },
    {
        "owner": "build", "name": "hello-build", "ecosystem": Ecosystem.MAVEN,
        "pillars": (80, 22, 26, 34, 70),
        "stars": 65, "forks": 8, "downloads": 21_000, "dependents": 55,
        "ef": 1, "legal": LegalIPModel.DCO, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 11,
    },
    # ---- Graveyard (low leverage / high friction) ------------------------ #
    {
        "owner": "dust", "name": "dusted-lib", "ecosystem": Ecosystem.PYPI,
        "pillars": (10, 15, 20, 10, 15),
        "stars": 380, "forks": 42, "downloads": 30_000, "dependents": 90,
        "ef": 1, "legal": LegalIPModel.DCO, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 4,
    },
    {
        "owner": "aband", "name": "abandoned-sdk", "ecosystem": Ecosystem.NPM,
        "pillars": (18, 20, 25, 18, 22),
        "stars": 950, "forks": 96, "downloads": 88_000, "dependents": 210,
        "ef": 1, "legal": LegalIPModel.DCO, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 6,
    },
    {
        "owner": "stale", "name": "stale-tool", "ecosystem": Ecosystem.CRATES,
        "pillars": (12, 12, 18, 14, 16),
        "stars": 15, "forks": 3, "downloads": 7_000, "dependents": 9,
        "ef": 1, "legal": LegalIPModel.DCO, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 3,
    },
    {
        "owner": "rot", "name": "rot-framework", "ecosystem": Ecosystem.GO,
        "pillars": (22, 28, 30, 24, 26),
        "stars": 2100, "forks": 160, "downloads": 150_000, "dependents": 480,
        "ef": 2, "legal": LegalIPModel.DCO, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 12,
    },
    {
        "owner": "sorrows", "name": "jar-of-sorrows", "ecosystem": Ecosystem.MAVEN,
        "pillars": (16, 24, 14, 20, 12),
        "stars": 540, "forks": 37, "downloads": 44_000, "dependents": 130,
        "ef": 1, "legal": LegalIPModel.DCO, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 5,
    },
    # ---- Gated black hole (gate 2: GR_14 = 3/4 > 0.5) -------------------- #
    {
        "owner": "ghost", "name": "ghost-maint", "ecosystem": Ecosystem.PYPI,
        # Pillar pins are unreachable: the stage-0 gate short-circuits and
        # the engine stores the zero decomposition (leverage 0 / friction 100).
        "pillars": (50, 50, 50, 50, 50),
        "stars": 31000, "forks": 2400, "downloads": 5_100_000, "dependents": 21_000,
        "ef": 4, "legal": LegalIPModel.DCO, "ai": AIPosture.AD_HOC,
        "off_platform": False, "commits": 15,
        "pr_events": (
            # 3 of 4 open external PRs are >14 days old with zero maintainer
            # review comments -> ghost rate 0.75 > GHOST_RATE_GATE (0.50).
            RawPREvent(
                repo_owner="ghost", repo_name="ghost-maint", pr_number=101,
                created_at=DEMO_NOW - timedelta(days=90),
                author_login="contrib-a", author_association=AuthorAssociation.NONE,
                is_external=True, maintainer_review_comments=0,
            ),
            RawPREvent(
                repo_owner="ghost", repo_name="ghost-maint", pr_number=102,
                created_at=DEMO_NOW - timedelta(days=60),
                author_login="contrib-b", author_association=AuthorAssociation.NONE,
                is_external=True, maintainer_review_comments=0,
            ),
            RawPREvent(
                repo_owner="ghost", repo_name="ghost-maint", pr_number=103,
                created_at=DEMO_NOW - timedelta(days=30),
                author_login="contrib-c", author_association=AuthorAssociation.NONE,
                is_external=True, maintainer_review_comments=0,
            ),
            # Open but young (<14 days): not a ghost yet.
            RawPREvent(
                repo_owner="ghost", repo_name="ghost-maint", pr_number=104,
                created_at=DEMO_NOW - timedelta(days=3),
                author_login="contrib-d", author_association=AuthorAssociation.NONE,
                is_external=True, maintainer_review_comments=0,
            ),
        ),
    },
    # ---- Off-platform repo #2 (mailing-list workflow, Sweet Spot) -------- #
    {
        "owner": "mailcore", "name": "mail-list-core", "ecosystem": Ecosystem.SYSTEMS,
        "pillars": (70, 85, 80, 75, 66),
        "stars": 12000, "forks": 640, "downloads": 720_000, "dependents": 5_300,
        "ef": 5, "legal": LegalIPModel.FOUNDATION_CLA, "ai": AIPosture.PROSCRIBED,
        "off_platform": True, "commits": 300,
    },
)


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #
def _pinned_scorers(pillars: tuple[float, ...]) -> dict[str, Callable[..., float]]:
    """Per-repo ``pillar_scorers`` DI seam (porchlamp.engine.score_repository).

    Each replacement is a zero-arg closure returning that repo's pinned
    pillar value; it is called with the same arguments as the default
    scorers, so the real pipeline (stage-0 gates, Phase-4 decomposition,
    Phase-5 balanced projection) runs end to end.
    """
    by_name = dict(zip(PILLAR_NAMES, pillars))

    def fixed(value: float) -> Callable[..., float]:
        def _scorer(*_args: Any, **_kwargs: Any) -> float:
            return value
        return _scorer

    return {name: fixed(by_name[name]) for name in PILLAR_NAMES}


def _synthetic_pr_events(
    owner: str,
    name: str,
    p1: float,
    now: datetime,
) -> list[RawPREvent]:
    """Deterministic PR population matching the fixture repo's responsiveness.

    Provides realistic Kaplan-Meier survival curves and raw metrics for deep-dive
    inspection while maintaining Stage-0 gate safety.
    """
    events: list[RawPREvent] = []
    if p1 >= 75:
        delays_days = [0.4, 0.8, 1.2, 1.8, 2.5, 3.2, 4.0]
        ttfhrs = [2.0, 4.5, 6.0, 8.0, 11.0, 14.0, 18.0]
    elif p1 >= 40:
        delays_days = [1.5, 2.8, 4.2, 5.5, 7.0, 9.5, 12.0]
        ttfhrs = [12.0, 18.0, 24.0, 32.0, 40.0, 48.0, 60.0]
    else:
        delays_days = [4.0, 7.5, 12.0, 16.0, 21.0, 28.0, 35.0]
        ttfhrs = [36.0, 48.0, 72.0, 96.0, 120.0, 144.0, 168.0]

    for idx, (delay, ttfhr) in enumerate(zip(delays_days, ttfhrs), start=1):
        created_at = now - timedelta(days=delay + 5.0)
        merged_at = created_at + timedelta(days=delay)
        events.append(
            RawPREvent(
                repo_owner=owner,
                repo_name=name,
                pr_number=idx,
                created_at=created_at,
                author_login=f"contrib-{idx}",
                author_association=AuthorAssociation.NONE,
                author_prior_merged_commits=0 if idx % 2 == 1 else 2,
                closed_at=None,
                merged_at=merged_at,
                is_external=True,
                response_latency_hours=ttfhr,
                maintainer_review_comments=max(1, 4 - int(delay // 3)),
            )
        )
    return events


def seed_fixture_store(store: OperationalStore) -> int:
    """Score + upsert + hydrate every fixture repo; return the row count."""
    for fixture in _FIXTURES:
        p1 = fixture["pillars"][0]
        gfis = fixture.get(
            "gfis",
            14 if p1 >= 75 else (5 if p1 >= 40 else 0),
        )
        meta = RepoMeta(
            owner=fixture["owner"],
            name=fixture["name"],
            ecosystem=fixture["ecosystem"],
            stars=fixture["stars"],
            forks=fixture["forks"],
            monthly_downloads=fixture["downloads"],
            dependents_count=fixture["dependents"],
            elephant_factor=fixture["ef"],
            legal_ip_model=fixture["legal"],
            ai_posture=fixture["ai"],
            active_nonbot_commits_180d=fixture["commits"],
            off_platform_reviews=fixture["off_platform"],
            active_unclaimed_gfis=gfis,
        )
        if "pr_events" in fixture:
            pr_events = list(fixture["pr_events"])
        else:
            pr_events = _synthetic_pr_events(
                fixture["owner"], fixture["name"], p1, DEMO_NOW
            )

        result = score_repository(
            meta,
            pr_events,
            DEMO_NOW,
            persona=Persona.BALANCED,
            pillar_scorers=_pinned_scorers(fixture["pillars"]),
        )
        store.upsert_composite(
            result,
            pillar_scores=result.pillar_scores,
            last_indexed_at=DEMO_NOW,
        )
        apply_raw_metrics(
            store,
            meta.owner,
            meta.name,
            stars=fixture["stars"],
            forks=fixture["forks"],
            monthly_downloads=fixture["downloads"],
            dependents_count=fixture["dependents"],
            elephant_factor=fixture["ef"],
            legal_ip_model=fixture["legal"],
            ai_posture=fixture["ai"],
        )
        deep_dive = build_deep_dive(meta, pr_events, DEMO_NOW)
        store.upsert_deep_dive(meta.owner, meta.name, deep_dive)
    return len(_FIXTURES)


def build_fixture_store(
    db_path: str = ":memory:",
    count: Optional[int] = None,
) -> OperationalStore:
    """Create a store (in-memory by default) and seed repositories.

    If ``count`` is None, seeds the original 26 fixtures for test compatibility;
    if ``count`` is provided, seeds that many benchmark repositories.
    """
    if count is not None and count != 26:
        return build_benchmark_store(db_path, count=count)
    store = OperationalStore(db_path)
    seed_fixture_store(store)
    return store


# --------------------------------------------------------------------------- #
# App + entry point
# --------------------------------------------------------------------------- #
def create_demo_app(
    store: Optional[OperationalStore] = None,
    count: int = 1000,
) -> FastAPI:
    """Porchlamp API + the offline web UI mounted at ``/`` (API routes win)."""
    if store is None:
        store = build_benchmark_store(count=count)
    app = create_app(store=store)
    app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")
    return app


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Porchlamp demo: fixture data + web UI")
    parser.add_argument(
        "--db",
        default=":memory:",
        help="SQLite path for the OperationalStore (default: in-memory).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1000,
        help="Number of benchmark repositories to seed (default: 1000).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", 8000)),
        help="Port to serve on (default: $PORT or 8000).",
    )
    args = parser.parse_args(argv)
    store = build_benchmark_store(args.db, count=args.count)
    app = create_demo_app(store, count=args.count)
    print(f"Porchlamp demo: seeded {args.count} benchmark repos (db={args.db!r})")
    print(f"Porchlamp demo: serving http://127.0.0.1:{args.port}/ (API under /v1, /healthz)")
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
