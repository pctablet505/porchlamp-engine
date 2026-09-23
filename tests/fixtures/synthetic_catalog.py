"""Top 1,000 Benchmark Dataset generator and store seeder.

Comprehensive, realistic benchmark catalog of 1,000 open-source repositories across
6 ecosystems (PyPI, npm, Crates, Go, Maven, Systems) and 4 quadrants:
- Sweet Spot (~35%): High leverage (>=50), low friction (<50), Tier S+/S/A
- Crucible (~15%): High leverage (>=50), high friction (>=50), Tier A/B
- Nursery (~25%): Low leverage (<50), low friction (<50), Tier B/C
- Graveyard (~25%): Low leverage (<50), high friction (>=50), Tier C/D (incl. gated black holes)

Includes ground-truth reference projects:
- vllm-project/vllm (Sweet Spot, Tier S, Porchlamp 88.8)
- keras-team/keras-hub (Graveyard, Tier D, 0.0 gated black hole)
- torvalds/linux (Crucible, off-platform)
- gentoo/gentoo (AI-proscribed, Tier A under BALANCED)

Fast initialization: seeding all 1,000 repos into OperationalStore completes in <0.2s.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from porchlamp.deepdive import build_deep_dive
from porchlamp.engine import PILLAR_NAMES, assign_tier
from porchlamp.lakehouse import OperationalStore
from porchlamp.matrix import decompose
from porchlamp.models import (
    AIPosture,
    AuthorAssociation,
    Ecosystem,
    LegalIPModel,
    Persona,
    PillarScores,
    Quadrant,
    RawPREvent,
    RepoMeta,
    Tier,
)
from porchlamp.persona import persona_composite

__all__ = [
    "BENCHMARK_NOW",
    "generate_benchmark_catalog",
    "generate_synthetic_pr_events",
    "get_benchmark_catalog",
    "seed_benchmark_catalog",
    "seed_benchmark_store",
    "build_benchmark_store",
]

BENCHMARK_NOW: datetime = datetime(2026, 9, 11, 12, 0, 0)

# Prominent open-source benchmark repositories across all 6 ecosystems.
# Format: (owner, name, ecosystem, quadrant, tier, pillars, stars, forks, downloads, dependents, ef, legal, ai, is_gated, off_platform, hard_ai_ban)
_PROMINENT_REPOSITORIES: tuple[tuple[Any, ...], ...] = (
    # ---- PYPI --------------------------------------------------------------- #
    (
        "vllm-project", "vllm", Ecosystem.PYPI, Quadrant.SWEET_SPOT, Tier.S,
        (92.0, 88.0, 90.0, 86.0, 86.0), 38500, 5400, 12000000, 8500, 4,
        LegalIPModel.DCO, AIPosture.REGULATED, False, False, False,
    ),
    (
        "keras-team", "keras-hub", Ecosystem.PYPI, Quadrant.GRAVEYARD, Tier.D,
        (45.0, 56.2, 40.0, 45.0, 45.0), 11200, 1400, 1800000, 2100, 1,
        LegalIPModel.CORP_CLA_UNILATERAL, AIPosture.AD_HOC, True, False, False,
    ),
    (
        "tiangolo", "fastapi", Ecosystem.PYPI, Quadrant.SWEET_SPOT, Tier.S,
        (88.0, 86.0, 84.0, 82.0, 85.0), 78000, 6200, 28000000, 45000, 4,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "huggingface", "transformers", Ecosystem.PYPI, Quadrant.SWEET_SPOT, Tier.S_PLUS,
        (94.0, 95.0, 92.0, 90.0, 91.0), 132000, 25000, 45000000, 62000, 5,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "pytorch", "pytorch", Ecosystem.PYPI, Quadrant.SWEET_SPOT, Tier.S_PLUS,
        (92.0, 96.0, 94.0, 90.0, 90.0), 85000, 22000, 38000000, 75000, 5,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "django", "django", Ecosystem.PYPI, Quadrant.SWEET_SPOT, Tier.S,
        (86.0, 88.0, 86.0, 84.0, 84.0), 81000, 32000, 18000000, 50000, 4,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "pallets", "flask", Ecosystem.PYPI, Quadrant.SWEET_SPOT, Tier.A,
        (76.0, 78.0, 76.0, 74.0, 75.0), 67000, 16000, 22000000, 42000, 3,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "pydantic", "pydantic", Ecosystem.PYPI, Quadrant.SWEET_SPOT, Tier.S,
        (88.0, 85.0, 85.0, 84.0, 86.0), 22000, 2100, 55000000, 85000, 4,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "celery", "celery", Ecosystem.PYPI, Quadrant.CRUCIBLE, Tier.B,
        (20.0, 90.0, 75.0, 86.0, 25.0), 24500, 4800, 14000000, 22000, 3,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "pandas-dev", "pandas", Ecosystem.PYPI, Quadrant.CRUCIBLE, Tier.A,
        (14.0, 98.0, 92.0, 96.0, 65.0), 44000, 17500, 65000000, 95000, 5,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "numpy", "numpy", Ecosystem.PYPI, Quadrant.CRUCIBLE, Tier.A,
        (12.0, 99.0, 92.0, 96.0, 65.0), 27000, 9500, 85000000, 120000, 5,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "scikit-learn", "scikit-learn", Ecosystem.PYPI, Quadrant.SWEET_SPOT, Tier.S,
        (84.0, 88.0, 86.0, 84.0, 82.0), 59000, 25000, 32000000, 48000, 4,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "psf", "requests", Ecosystem.PYPI, Quadrant.SWEET_SPOT, Tier.A,
        (74.0, 78.0, 76.0, 72.0, 74.0), 52000, 9200, 75000000, 110000, 3,
        LegalIPModel.FOUNDATION_CLA, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "astral-sh", "ruff", Ecosystem.PYPI, Quadrant.SWEET_SPOT, Tier.S,
        (89.0, 85.0, 84.0, 85.0, 86.0), 34000, 1200, 15000000, 12000, 4,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "astral-sh", "uv", Ecosystem.PYPI, Quadrant.SWEET_SPOT, Tier.S_PLUS,
        (94.0, 93.0, 90.0, 92.0, 91.0), 42000, 1400, 18000000, 8000, 4,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    # ---- NPM ---------------------------------------------------------------- #
    (
        "facebook", "react", Ecosystem.NPM, Quadrant.SWEET_SPOT, Tier.S_PLUS,
        (96.0, 98.0, 92.0, 94.0, 92.0), 228000, 46000, 95000000, 240000, 5,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "vercel", "next.js", Ecosystem.NPM, Quadrant.SWEET_SPOT, Tier.S_PLUS,
        (93.0, 94.0, 90.0, 91.0, 90.0), 125000, 27000, 48000000, 85000, 5,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "vuejs", "core", Ecosystem.NPM, Quadrant.SWEET_SPOT, Tier.S,
        (86.0, 88.0, 85.0, 84.0, 85.0), 46000, 8200, 22000000, 55000, 4,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "sveltejs", "svelte", Ecosystem.NPM, Quadrant.SWEET_SPOT, Tier.S,
        (88.0, 84.0, 85.0, 84.0, 86.0), 79000, 4100, 12000000, 28000, 4,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "expressjs", "express", Ecosystem.NPM, Quadrant.NURSERY, Tier.B,
        (86.0, 32.0, 44.0, 35.0, 88.0), 65000, 14000, 85000000, 180000, 2,
        LegalIPModel.FOUNDATION_CLA, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "microsoft", "typescript", Ecosystem.NPM, Quadrant.CRUCIBLE, Tier.A,
        (14.0, 98.0, 92.0, 96.0, 65.0), 101000, 12500, 95000000, 220000, 5,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "tailwindlabs", "tailwindcss", Ecosystem.NPM, Quadrant.SWEET_SPOT, Tier.S,
        (87.0, 86.0, 85.0, 84.0, 85.0), 83000, 4200, 32000000, 65000, 4,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "vitejs", "vite", Ecosystem.NPM, Quadrant.SWEET_SPOT, Tier.S,
        (88.0, 87.0, 85.0, 84.0, 86.0), 71000, 6100, 38000000, 72000, 4,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "webpack", "webpack", Ecosystem.NPM, Quadrant.CRUCIBLE, Tier.B,
        (22.0, 91.0, 74.0, 86.0, 24.0), 64000, 8600, 65000000, 140000, 3,
        LegalIPModel.FOUNDATION_CLA, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "lodash", "lodash", Ecosystem.NPM, Quadrant.NURSERY, Tier.C,
        (76.0, 18.0, 28.0, 23.0, 78.0), 59000, 7100, 98000000, 250000, 1,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "axios", "axios", Ecosystem.NPM, Quadrant.SWEET_SPOT, Tier.A,
        (76.0, 76.0, 74.0, 72.0, 74.0), 105000, 10800, 92000000, 190000, 3,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "trpc", "trpc", Ecosystem.NPM, Quadrant.SWEET_SPOT, Tier.A,
        (78.0, 74.0, 74.0, 72.0, 75.0), 35000, 1300, 5500000, 14000, 3,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "electron", "electron", Ecosystem.NPM, Quadrant.CRUCIBLE, Tier.A,
        (14.0, 97.0, 92.0, 95.0, 65.0), 114000, 15200, 18000000, 35000, 5,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    # ---- CRATES ------------------------------------------------------------- #
    (
        "tokio-rs", "tokio", Ecosystem.CRATES, Quadrant.SWEET_SPOT, Tier.S_PLUS,
        (93.0, 95.0, 90.0, 92.0, 91.0), 26500, 2500, 12000000, 48000, 4,
        LegalIPModel.DCO, AIPosture.REGULATED, False, False, False,
    ),
    (
        "serde-rs", "serde", Ecosystem.CRATES, Quadrant.SWEET_SPOT, Tier.S,
        (87.0, 88.0, 85.0, 84.0, 85.0), 9800, 1100, 18000000, 65000, 3,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "BurntSushi", "ripgrep", Ecosystem.CRATES, Quadrant.SWEET_SPOT, Tier.S,
        (88.0, 85.0, 86.0, 84.0, 86.0), 48000, 2100, 5000000, 12000, 3,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "dtolnay", "syn", Ecosystem.CRATES, Quadrant.SWEET_SPOT, Tier.A,
        (76.0, 77.0, 75.0, 73.0, 74.0), 4100, 420, 16000000, 52000, 2,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "clap-rs", "clap", Ecosystem.CRATES, Quadrant.SWEET_SPOT, Tier.A,
        (78.0, 75.0, 74.0, 72.0, 76.0), 14200, 1100, 9500000, 31000, 3,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "tokio-rs", "axum", Ecosystem.CRATES, Quadrant.SWEET_SPOT, Tier.S,
        (88.0, 84.0, 85.0, 84.0, 86.0), 19500, 1200, 6200000, 16000, 4,
        LegalIPModel.DCO, AIPosture.REGULATED, False, False, False,
    ),
    (
        "actix", "actix-web", Ecosystem.CRATES, Quadrant.CRUCIBLE, Tier.A,
        (14.0, 97.0, 91.0, 95.0, 65.0), 21500, 2400, 7800000, 18000, 4,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "rust-random", "rand", Ecosystem.CRATES, Quadrant.SWEET_SPOT, Tier.A,
        (75.0, 78.0, 75.0, 73.0, 74.0), 5200, 680, 14000000, 45000, 3,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "pola-rs", "polars", Ecosystem.CRATES, Quadrant.SWEET_SPOT, Tier.S_PLUS,
        (94.0, 93.0, 91.0, 91.0, 91.0), 31000, 2100, 11000000, 22000, 4,
        LegalIPModel.DCO, AIPosture.REGULATED, False, False, False,
    ),
    (
        "bytecodealliance", "wasmtime", Ecosystem.CRATES, Quadrant.CRUCIBLE, Tier.A,
        (14.0, 98.0, 92.0, 96.0, 65.0), 15800, 1400, 4500000, 11000, 5,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "seanmonstar", "reqwest", Ecosystem.CRATES, Quadrant.SWEET_SPOT, Tier.S,
        (86.0, 86.0, 85.0, 84.0, 85.0), 10500, 1100, 13000000, 38000, 3,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "tauri-apps", "tauri", Ecosystem.CRATES, Quadrant.SWEET_SPOT, Tier.S,
        (88.0, 85.0, 85.0, 84.0, 87.0), 82000, 3100, 5500000, 15000, 4,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    # ---- GO ----------------------------------------------------------------- #
    (
        "kubernetes", "kubernetes", Ecosystem.GO, Quadrant.CRUCIBLE, Tier.A,
        (14.0, 99.0, 92.0, 96.0, 65.0), 112000, 39500, 45000000, 85000, 5,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "prometheus", "prometheus", Ecosystem.GO, Quadrant.SWEET_SPOT, Tier.S,
        (87.0, 88.0, 85.0, 84.0, 85.0), 56000, 9100, 22000000, 38000, 5,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "etcd-io", "etcd", Ecosystem.GO, Quadrant.CRUCIBLE, Tier.A,
        (14.0, 98.0, 91.0, 95.0, 65.0), 47000, 9800, 18000000, 29000, 5,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "gin-gonic", "gin", Ecosystem.GO, Quadrant.SWEET_SPOT, Tier.A,
        (76.0, 77.0, 74.0, 72.0, 75.0), 78000, 8100, 28000000, 42000, 3,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "gohugoio", "hugo", Ecosystem.GO, Quadrant.SWEET_SPOT, Tier.S,
        (88.0, 85.0, 85.0, 84.0, 86.0), 75000, 7500, 11000000, 18000, 4,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "moby", "moby", Ecosystem.GO, Quadrant.CRUCIBLE, Tier.B,
        (18.0, 92.0, 75.0, 87.0, 25.0), 68000, 18500, 32000000, 55000, 4,
        LegalIPModel.FOUNDATION_CLA, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "hashicorp", "terraform", Ecosystem.GO, Quadrant.CRUCIBLE, Tier.A,
        (14.0, 98.0, 92.0, 96.0, 65.0), 42000, 9200, 35000000, 60000, 5,
        LegalIPModel.CORP_CLA_RECIPROCAL, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "caddyserver", "caddy", Ecosystem.GO, Quadrant.SWEET_SPOT, Tier.S,
        (88.0, 85.0, 85.0, 84.0, 86.0), 57000, 4100, 9500000, 14000, 4,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "traefik", "traefik", Ecosystem.GO, Quadrant.SWEET_SPOT, Tier.S,
        (87.0, 86.0, 85.0, 84.0, 86.0), 51000, 5200, 14000000, 22000, 4,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "cockroachdb", "cockroach", Ecosystem.GO, Quadrant.CRUCIBLE, Tier.A,
        (14.0, 98.0, 92.0, 96.0, 65.0), 30000, 4100, 8500000, 12000, 5,
        LegalIPModel.CORP_CLA_RECIPROCAL, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "junegunn", "fzf", Ecosystem.GO, Quadrant.SWEET_SPOT, Tier.A,
        (78.0, 74.0, 74.0, 72.0, 76.0), 64000, 2400, 6500000, 9500, 3,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    # ---- MAVEN -------------------------------------------------------------- #
    (
        "spring-projects", "spring-boot", Ecosystem.MAVEN, Quadrant.SWEET_SPOT, Tier.S_PLUS,
        (93.0, 96.0, 92.0, 91.0, 90.0), 75000, 41000, 55000000, 110000, 5,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "apache", "kafka", Ecosystem.MAVEN, Quadrant.CRUCIBLE, Tier.A,
        (14.0, 99.0, 92.0, 96.0, 65.0), 29000, 13500, 38000000, 75000, 5,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "apache", "spark", Ecosystem.MAVEN, Quadrant.CRUCIBLE, Tier.A,
        (14.0, 98.0, 92.0, 96.0, 65.0), 39000, 27500, 32000000, 62000, 5,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "apache", "flink", Ecosystem.MAVEN, Quadrant.CRUCIBLE, Tier.A,
        (14.0, 98.0, 91.0, 95.0, 65.0), 24000, 13200, 19000000, 31000, 5,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "google", "guice", Ecosystem.MAVEN, Quadrant.SWEET_SPOT, Tier.A,
        (76.0, 77.0, 74.0, 72.0, 75.0), 12500, 2100, 25000000, 48000, 4,
        LegalIPModel.CORP_CLA_RECIPROCAL, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "apache", "logging-log4j2", Ecosystem.MAVEN, Quadrant.CRUCIBLE, Tier.B,
        (20.0, 92.0, 75.0, 87.0, 25.0), 3800, 1900, 42000000, 85000, 5,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "netty", "netty", Ecosystem.MAVEN, Quadrant.CRUCIBLE, Tier.A,
        (14.0, 98.0, 92.0, 96.0, 65.0), 33500, 16000, 45000000, 92000, 5,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "elastic", "elasticsearch", Ecosystem.MAVEN, Quadrant.CRUCIBLE, Tier.A,
        (14.0, 98.0, 92.0, 96.0, 65.0), 69000, 24500, 28000000, 45000, 5,
        LegalIPModel.CORP_CLA_RECIPROCAL, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "apache", "dubbo", Ecosystem.MAVEN, Quadrant.CRUCIBLE, Tier.B,
        (22.0, 91.0, 74.0, 86.0, 24.0), 40500, 26000, 12000000, 21000, 4,
        LegalIPModel.FOUNDATION_CLA, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "apache", "hadoop", Ecosystem.MAVEN, Quadrant.GRAVEYARD, Tier.C,
        (24.0, 47.0, 47.0, 47.0, 44.0), 14500, 8900, 15000000, 28000, 4,
        LegalIPModel.FOUNDATION_CLA, AIPosture.AD_HOC, False, False, False,
    ),
    # ---- SYSTEMS ------------------------------------------------------------ #
    (
        "torvalds", "linux", Ecosystem.SYSTEMS, Quadrant.CRUCIBLE, Tier.B,
        (25.0, 95.0, 80.0, 92.0, 30.0), 182000, 54000, 48000000, 95000, 5,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, True, False,
    ),
    (
        "gentoo", "gentoo", Ecosystem.SYSTEMS, Quadrant.SWEET_SPOT, Tier.A,
        (80.0, 80.0, 70.0, 80.0, 60.0), 2600, 1200, 650000, 3100, 4,
        LegalIPModel.DCO, AIPosture.PROSCRIBED, False, False, True,
    ),
    (
        "chromium", "chromium", Ecosystem.SYSTEMS, Quadrant.CRUCIBLE, Tier.A,
        (14.0, 99.0, 92.0, 96.0, 65.0), 16000, 4800, 35000000, 52000, 5,
        LegalIPModel.CORP_CLA_RECIPROCAL, AIPosture.REGULATED, False, False, False,
    ),
    (
        "qemu", "qemu", Ecosystem.SYSTEMS, Quadrant.CRUCIBLE, Tier.A,
        (14.0, 98.0, 91.0, 95.0, 65.0), 11000, 3500, 12000000, 24000, 5,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "freebsd", "freebsd-src", Ecosystem.SYSTEMS, Quadrant.CRUCIBLE, Tier.B,
        (20.0, 92.0, 75.0, 87.0, 25.0), 5800, 2100, 4500000, 12000, 4,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "llvm", "llvm-project", Ecosystem.SYSTEMS, Quadrant.CRUCIBLE, Tier.A,
        (14.0, 99.0, 92.0, 96.0, 65.0), 28000, 11500, 38000000, 85000, 5,
        LegalIPModel.FOUNDATION_CLA, AIPosture.REGULATED, False, False, False,
    ),
    (
        "gcc-mirror", "gcc", Ecosystem.SYSTEMS, Quadrant.CRUCIBLE, Tier.B,
        (18.0, 94.0, 75.0, 88.0, 25.0), 7200, 2900, 15000000, 45000, 4,
        LegalIPModel.FOUNDATION_CLA, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "sqlite", "sqlite", Ecosystem.SYSTEMS, Quadrant.SWEET_SPOT, Tier.S,
        (88.0, 87.0, 86.0, 84.0, 85.0), 28000, 3400, 65000000, 120000, 5,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "git", "git", Ecosystem.SYSTEMS, Quadrant.CRUCIBLE, Tier.A,
        (14.0, 98.0, 92.0, 96.0, 65.0), 53000, 25500, 52000000, 110000, 5,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "redis", "redis", Ecosystem.SYSTEMS, Quadrant.SWEET_SPOT, Tier.S,
        (87.0, 88.0, 85.0, 84.0, 85.0), 66000, 23000, 42000000, 88000, 4,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
    (
        "postgres", "postgres", Ecosystem.SYSTEMS, Quadrant.CRUCIBLE, Tier.A,
        (14.0, 99.0, 92.0, 96.0, 65.0), 16500, 5100, 48000000, 95000, 5,
        LegalIPModel.DCO, AIPosture.AD_HOC, False, False, False,
    ),
)


# Name generator dictionaries per ecosystem.
_ECO_OWNERS: dict[Ecosystem, list[str]] = {
    Ecosystem.PYPI: [
        "py-data", "sciverse", "deeplearn", "fastcore", "pylogic", "pyasync",
        "neuro-ai", "pygraph", "tensor-flow", "quant-lab", "pysec", "datalab",
        "stream-py", "algorun", "pysparkle", "scipy-contrib",
    ],
    Ecosystem.NPM: [
        "web-infra", "ui-kit", "front-craft", "js-tools", "node-mesh", "react-tools",
        "dev-stack", "fullstack", "vibe-dev", "hyper-web", "next-tools", "cloud-ui",
        "open-craft", "byte-wave", "style-core", "state-craft",
    ],
    Ecosystem.CRATES: [
        "rust-core", "iron-forge", "safe-sys", "rusty-bytes", "ferris-labs", "cargo-pkg",
        "warp-speed", "async-rs", "zero-copy", "embedded-rs", "crypto-rs", "simd-rs",
        "parser-rs", "memory-rs", "net-rs", "system-rs",
    ],
    Ecosystem.GO: [
        "go-infra", "cloud-native", "micro-mesh", "kube-tools", "go-stream", "net-core",
        "dist-sys", "packet-go", "fast-rpc", "raft-core", "etcd-contrib", "fiber-labs",
        "echo-tools", "zero-alloc", "concur-go", "grpc-tools",
    ],
    Ecosystem.MAVEN: [
        "jvm-infra", "cloud-java", "enterprise-lib", "data-pipeline", "spring-contrib", "quarkus-dev",
        "stream-jvm", "async-java", "byte-buddy-labs", "reactive-jvm", "micrometer-contrib", "jakarta-tools",
        "apache-commons-x", "netty-contrib", "kafka-streams-x", "grpc-java-x",
    ],
    Ecosystem.SYSTEMS: [
        "sys-kernel", "embedded-core", "unix-tools", "driver-labs", "os-runtime", "hardware-io",
        "lowlevel-dev", "native-sys", "firmware-lab", "glibc-contrib", "posix-core", "boot-tools",
        "arch-ports", "compiler-dev", "micro-kernel", "bare-metal",
    ],
}

_ECO_STEMS: dict[Ecosystem, list[str]] = {
    Ecosystem.PYPI: [
        "tensor-opt", "async-worker", "data-frame", "vector-db", "ml-pipeline", "graph-query",
        "vision-core", "audio-stream", "nlp-token", "schema-sync", "cache-redis", "sql-orm",
        "http-client", "job-queue", "cron-task", "crypto-hash", "config-yaml", "auth-token",
        "perf-trace", "metric-stat", "event-loop", "log-writer", "rpc-client", "thread-pool", "net-socket",
    ],
    Ecosystem.NPM: [
        "state-machine", "form-builder", "modal-dialog", "table-grid", "router-dom", "animation-spring",
        "color-theme", "date-picker", "icon-bundle", "rich-editor", "virtual-list", "canvas-draw",
        "audio-player", "markdown-render", "toast-notify", "drag-drop", "cookie-store", "i18n-locale",
        "query-cache", "hook-utils", "chart-svg", "dropdown-menu", "tooltip-box", "image-lazy", "event-emitter",
    ],
    Ecosystem.CRATES: [
        "ring-buffer", "bit-set", "hash-map", "thread-lock", "channel-mpsc", "atomic-ref",
        "byte-pool", "tcp-stream", "udp-socket", "tls-cert", "sha-digest", "aes-cipher",
        "lz4-compress", "zstd-decompress", "utf8-validator", "regex-engine", "json-lexer", "yaml-emitter",
        "toml-parser", "ast-node", "arena-alloc", "fut-runtime", "task-spawn", "sync-barrier", "slab-alloc",
    ],
    Ecosystem.GO: [
        "service-mesh", "load-balancer", "circuit-breaker", "rate-limiter", "health-check", "dns-resolver",
        "grpc-gateway", "metric-exporter", "trace-collector", "leader-election", "raft-consensus", "kv-store",
        "wal-logger", "conn-pool", "worker-pool", "buffer-pool", "retry-backoff", "middleware-auth",
        "event-dispatcher", "scheduler-cron", "config-loader", "secret-vault", "cluster-mgr", "node-registry", "stream-pipeline",
    ],
    Ecosystem.MAVEN: [
        "connection-pool", "thread-pool-executor", "message-broker", "cache-manager", "data-source-router", "transaction-coordinator",
        "metrics-registry", "distributed-lock", "event-stream-processor", "object-mapper", "validator-framework", "security-filter",
        "rpc-invoker", "batch-chunk-reader", "async-completable", "retry-template", "circuit-breaker-resilience", "logging-appender",
        "protocol-decoder", "reactive-subscriber", "cluster-membership", "snapshot-storage", "query-engine", "dependency-injector", "rest-template-client",
    ],
    Ecosystem.SYSTEMS: [
        "mem-allocator", "pci-driver", "usb-controller", "dma-channel", "virtio-net", "nvme-block",
        "interrupt-handler", "page-table", "process-scheduler", "vfs-layer", "ext4-parser", "btrfs-scrub",
        "socket-raw", "packet-filter", "crypto-accelerator", "watchdog-timer", "acpi-parser", "elf-loader",
        "debug-trace", "syscall-shim", "atomic-ops", "spin-lock", "futex-wait", "tlb-flush", "boot-loader",
    ],
}

_NAME_SUFFIXES: tuple[str, ...] = (
    "core", "engine", "client", "runtime", "lib", "toolkit", "plugin",
    "driver", "bridge", "service", "gateway", "handler", "manager",
    "router", "builder", "scanner", "proxy",
)

# Canonical target breakdown per (Ecosystem, Quadrant). Sums to exactly 1,000.
_QUAD_ECO_TARGETS: dict[tuple[Ecosystem, Quadrant], int] = {
    (Ecosystem.PYPI, Quadrant.SWEET_SPOT): 61,
    (Ecosystem.PYPI, Quadrant.CRUCIBLE): 26,
    (Ecosystem.PYPI, Quadrant.NURSERY): 44,
    (Ecosystem.PYPI, Quadrant.GRAVEYARD): 44,

    (Ecosystem.NPM, Quadrant.SWEET_SPOT): 61,
    (Ecosystem.NPM, Quadrant.CRUCIBLE): 26,
    (Ecosystem.NPM, Quadrant.NURSERY): 44,
    (Ecosystem.NPM, Quadrant.GRAVEYARD): 44,

    (Ecosystem.CRATES, Quadrant.SWEET_SPOT): 58,
    (Ecosystem.CRATES, Quadrant.CRUCIBLE): 25,
    (Ecosystem.CRATES, Quadrant.NURSERY): 41,
    (Ecosystem.CRATES, Quadrant.GRAVEYARD): 41,

    (Ecosystem.GO, Quadrant.SWEET_SPOT): 58,
    (Ecosystem.GO, Quadrant.CRUCIBLE): 25,
    (Ecosystem.GO, Quadrant.NURSERY): 41,
    (Ecosystem.GO, Quadrant.GRAVEYARD): 41,

    (Ecosystem.MAVEN, Quadrant.SWEET_SPOT): 56,
    (Ecosystem.MAVEN, Quadrant.CRUCIBLE): 24,
    (Ecosystem.MAVEN, Quadrant.NURSERY): 40,
    (Ecosystem.MAVEN, Quadrant.GRAVEYARD): 40,

    (Ecosystem.SYSTEMS, Quadrant.SWEET_SPOT): 56,
    (Ecosystem.SYSTEMS, Quadrant.CRUCIBLE): 24,
    (Ecosystem.SYSTEMS, Quadrant.NURSERY): 40,
    (Ecosystem.SYSTEMS, Quadrant.GRAVEYARD): 40,
}


def _make_pillars_for(quadrant: Quadrant, tier: Tier, index: int) -> tuple[float, float, float, float, float]:
    """Return pinned 5-pillar tuple guaranteed to land in (quadrant, tier)."""
    v = (index % 10) * 0.2  # 0.0 to 1.8 deterministic variance
    if quadrant == Quadrant.SWEET_SPOT:
        if tier == Tier.S_PLUS:
            p1, p2, p3, p4, p5 = 93.0 + v, 94.0 + v, 88.0 + v, 91.0 + v, 89.0 + v
        elif tier == Tier.S:
            p1, p2, p3, p4, p5 = 84.0 + v, 83.0 + v, 82.0 + v, 81.0 + v, 80.0 + v
        else:  # Tier A
            p1, p2, p3, p4, p5 = 74.0 + v, 73.0 + v, 72.0 + v, 71.0 + v, 72.0 + v
    elif quadrant == Quadrant.CRUCIBLE:
        if tier == Tier.A:
            p1, p2, p3, p4, p5 = 12.0 + (index % 5) * 0.4, 98.0, 92.0, 96.0, 65.0
        else:  # Tier B
            p1, p2, p3, p4, p5 = 18.0 + v, 91.0 + v, 74.0 + v, 87.0 + v, 24.0 + v
    elif quadrant == Quadrant.NURSERY:
        if tier == Tier.B:
            p1, p2, p3, p4, p5 = 86.0 + v, 30.0 + v, 44.0 + v, 35.0 + v, 88.0 + v
        else:  # Tier C
            p1, p2, p3, p4, p5 = 76.0 + v, 18.0 + v, 28.0 + v, 23.0 + v, 78.0 + v
    else:  # GRAVEYARD
        if tier == Tier.C:
            p1, p2, p3, p4, p5 = 24.0 + (index % 5) * 0.4, 47.0 + (index % 4) * 0.2, 47.0, 47.0, 44.0
        else:  # Tier D
            p1, p2, p3, p4, p5 = 8.0 + v, 12.0 + v, 18.0 + v, 12.0 + v, 12.0 + v
    return (round(p1, 2), round(p2, 2), round(p3, 2), round(p4, 2), round(p5, 2))


def _generate_synthetic_catalog() -> list[dict[str, Any]]:
    """Build the full, deterministic 1,000 repository catalog."""
    catalog: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    # Track how many we have per (ecosystem, quadrant)
    counts: dict[tuple[Ecosystem, Quadrant], int] = dict.fromkeys(_QUAD_ECO_TARGETS, 0)

    # 1. Add prominent repos first
    for item in _PROMINENT_REPOSITORIES:
        (
            owner, name, eco, quad, tier, pillars, stars, forks,
            downloads, dependents, ef, legal, ai, is_gated,
            off_platform, hard_ai_ban
        ) = item
        pair = (owner, name)
        seen.add(pair)
        counts[(eco, quad)] += 1
        catalog.append({
            "owner": owner,
            "name": name,
            "ecosystem": eco,
            "quadrant": quad,
            "tier": tier,
            "pillars": pillars,
            "stars": stars,
            "forks": forks,
            "monthly_downloads": downloads,
            "dependents_count": dependents,
            "elephant_factor": ef,
            "legal_ip_model": legal,
            "ai_posture": ai,
            "active_nonbot_commits_180d": 10 if is_gated else 350,
            "off_platform_reviews": off_platform,
            "hard_ai_ban": hard_ai_ban,
            "active_unclaimed_gfis": 15 if pillars[0] >= 75 else 5,
            "is_gated": is_gated,
        })

    # 2. Fill the remainder for each (ecosystem, quadrant) up to target
    gen_idx = 0
    for (eco, quad), target in _QUAD_ECO_TARGETS.items():
        needed = target - counts[(eco, quad)]
        for i in range(needed):
            # Select target tier for this quadrant slot
            if quad == Quadrant.SWEET_SPOT:
                tier = Tier.S_PLUS if i < 6 else (Tier.S if i < 24 else Tier.A)
            elif quad == Quadrant.CRUCIBLE:
                tier = Tier.A if i < 7 else Tier.B
            elif quad == Quadrant.NURSERY:
                tier = Tier.B if i < 16 else Tier.C
            else:  # GRAVEYARD
                tier = Tier.C if i < 18 else Tier.D

            # Gated black holes: up to index 3 in Graveyard Tier D
            is_gated = (quad == Quadrant.GRAVEYARD and tier == Tier.D and i < 21)

            pillars = _make_pillars_for(quad, tier, i)

            # Generate unique (owner, name)
            owner = _ECO_OWNERS[eco][gen_idx % len(_ECO_OWNERS[eco])]
            stem = _ECO_STEMS[eco][(gen_idx // len(_ECO_OWNERS[eco])) % len(_ECO_STEMS[eco])]
            suffix = _NAME_SUFFIXES[gen_idx % len(_NAME_SUFFIXES)]
            name = f"{stem}-{suffix}"
            while (owner, name) in seen:
                gen_idx += 1
                owner = _ECO_OWNERS[eco][gen_idx % len(_ECO_OWNERS[eco])]
                stem = _ECO_STEMS[eco][(gen_idx // len(_ECO_OWNERS[eco])) % len(_ECO_STEMS[eco])]
                suffix = _NAME_SUFFIXES[gen_idx % len(_NAME_SUFFIXES)]
                name = f"{stem}-{suffix}"
            gen_idx += 1
            seen.add((owner, name))

            # Realistic footprint metrics according to tier
            if tier in (Tier.S_PLUS, Tier.S):
                stars = 15000 + (i * 950) % 75000
                forks = max(50, stars // 8)
                downloads = 5000000 + (i * 350000) % 40000000
                dependents = 8000 + (i * 450) % 65000
                ef = 4 + (i % 3)
                legal = LegalIPModel.FOUNDATION_CLA if i % 2 == 0 else LegalIPModel.DCO
                ai = AIPosture.REGULATED if i % 3 == 0 else AIPosture.AD_HOC
            elif tier == Tier.A:
                stars = 4000 + (i * 450) % 25000
                forks = max(30, stars // 10)
                downloads = 1200000 + (i * 120000) % 15000000
                dependents = 2500 + (i * 150) % 20000
                ef = 3 + (i % 3)
                legal = LegalIPModel.DCO if i % 2 == 0 else LegalIPModel.FOUNDATION_CLA
                ai = AIPosture.AD_HOC
            elif tier == Tier.B:
                stars = 800 + (i * 180) % 12000
                forks = max(15, stars // 12)
                downloads = 300000 + (i * 40000) % 4000000
                dependents = 400 + (i * 60) % 6000
                ef = 2 + (i % 3)
                legal = LegalIPModel.DCO
                ai = AIPosture.AD_HOC
            elif tier == Tier.C:
                stars = 150 + (i * 65) % 3500
                forks = max(5, stars // 15)
                downloads = 40000 + (i * 8000) % 800000
                dependents = 30 + (i * 15) % 1200
                ef = 1 + (i % 2)
                legal = LegalIPModel.DCO
                ai = AIPosture.AD_HOC
            else:  # Tier D
                stars = 15 + (i * 25) % 1200
                forks = max(1, stars // 20)
                downloads = 2000 + (i * 1500) % 150000
                dependents = 2 + (i * 3) % 200
                ef = 1
                legal = LegalIPModel.DCO if i % 2 == 0 else LegalIPModel.CORP_CLA_UNILATERAL
                ai = AIPosture.AD_HOC

            catalog.append({
                "owner": owner,
                "name": name,
                "ecosystem": eco,
                "quadrant": quad,
                "tier": tier,
                "pillars": pillars,
                "stars": stars,
                "forks": forks,
                "monthly_downloads": downloads,
                "dependents_count": dependents,
                "elephant_factor": ef,
                "legal_ip_model": legal,
                "ai_posture": ai,
                "active_nonbot_commits_180d": 5 if is_gated else 120,
                "off_platform_reviews": False,
                "hard_ai_ban": False,
                "active_unclaimed_gfis": 10 if pillars[0] >= 75 else (3 if pillars[0] >= 40 else 0),
                "is_gated": is_gated,
            })

    return catalog


# Precomputed, immutable catalog of 1,000 repositories.
_CATALOG: list[dict[str, Any]] = _generate_synthetic_catalog()


def get_benchmark_catalog(count: int = 1000) -> list[dict[str, Any]]:
    """Return benchmark repository definitions up to ``count`` (default 1000)."""
    return _CATALOG[:count]


def generate_benchmark_catalog() -> list[dict[str, Any]]:
    """Return exactly 1,000 benchmark repository definitions."""
    return get_benchmark_catalog(1000)


def generate_synthetic_pr_events(
    owner: str,
    name: str,
    p1: float,
    now: datetime,
    is_gated: bool = False,
) -> list[RawPREvent]:
    """Deterministic PR population matching the repo's responsiveness and gating posture."""
    events: list[RawPREvent] = []
    if is_gated:
        # Tripped responsiveness ceiling: 3 open external PRs > 14 days old with 0 comments,
        # plus 1 recent clean open PR. GR_14 = 3/4 = 0.75 > 0.50.
        for idx, days_ago in enumerate([45.0, 30.0, 18.0], start=1):
            created_at = now - timedelta(days=days_ago)
            events.append(
                RawPREvent(
                    repo_owner=owner,
                    repo_name=name,
                    pr_number=idx,
                    created_at=created_at,
                    author_login=f"contrib-ghost-{idx}",
                    author_association=AuthorAssociation.NONE,
                    author_prior_merged_commits=0,
                    closed_at=None,
                    merged_at=None,
                    is_external=True,
                    response_latency_hours=None,
                    maintainer_review_comments=0,
                )
            )
        events.append(
            RawPREvent(
                repo_owner=owner,
                repo_name=name,
                pr_number=4,
                created_at=now - timedelta(days=2.0),
                author_login="contrib-recent",
                author_association=AuthorAssociation.NONE,
                author_prior_merged_commits=1,
                closed_at=None,
                merged_at=None,
                is_external=True,
                response_latency_hours=12.0,
                maintainer_review_comments=2,
            )
        )
        return events

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


def _pinned_scorers(pillars: tuple[float, ...]) -> dict[str, Callable[..., float]]:
    """Return DI scorers mapping each pillar name to its pinned constant."""
    by_name = dict(zip(PILLAR_NAMES, pillars))

    def fixed(val: float) -> Callable[..., float]:
        def _scorer(*_args: Any, **_kwargs: Any) -> float:
            return val
        return _scorer

    return {name: fixed(by_name[name]) for name in PILLAR_NAMES}


def seed_benchmark_catalog(
    store: OperationalStore, repos: list[dict[str, Any]], now: datetime
) -> None:
    """Bulk insert repository definitions in a single transaction in <2.0s.

    W4-1: moved verbatim from ``porchlamp.lakehouse.OperationalStore.
    seed_benchmark_catalog`` -- a production module (``porchlamp/lakehouse.py``)
    generating synthetic PR events and seeding a fabricated catalog is
    exactly the "production import of the fixture" this repo's
    non-negotiables forbid (synthetic data is a test fixture only). This
    is now a free function here instead, operating on ``store`` through
    its public API (``ensure_deep_dive_column()``, ``connection``) rather
    than living as a method on the class itself.

    ``now`` is the explicit reference time for the synthetic PR events
    and the ``last_indexed_at`` stamp (D-VER-3): the store owns no clock.
    """
    store.ensure_deep_dive_column()

    when = now
    when_iso = when.isoformat()
    rows = []

    for item in repos:
        pillars = item.get("pillars", (50.0, 50.0, 50.0, 50.0, 50.0))
        is_gated = item.get("is_gated", False)

        if is_gated:
            porchlamp = 0.0
            tier = Tier.D.value
            quadrant = Quadrant.GRAVEYARD.value
            lev = 0.0
            fric = 100.0
            is_dq = 1
            dq_reason = "PR ghost rate exceeds 50% ceiling (black hole)"
            p_scores = (0.0, 0.0, 0.0, 0.0, 0.0)
            ps = None
        else:
            ps = PillarScores(*pillars)
            lev, fric, quad = decompose(ps)
            porchlamp = persona_composite(ps, Persona.BALANCED)
            tier = assign_tier(porchlamp).value
            quadrant = quad.value
            is_dq = 0
            dq_reason = None
            p_scores = pillars

        meta = RepoMeta(
            owner=item["owner"],
            name=item["name"],
            ecosystem=item["ecosystem"],
            stars=item["stars"],
            forks=item["forks"],
            monthly_downloads=item["monthly_downloads"],
            dependents_count=item["dependents_count"],
            elephant_factor=item["elephant_factor"],
            legal_ip_model=item["legal_ip_model"],
            ai_posture=item["ai_posture"],
            active_nonbot_commits_180d=item.get("active_nonbot_commits_180d", 100),
            off_platform_reviews=item.get("off_platform_reviews", False),
            hard_ai_ban=item.get("hard_ai_ban", False),
            active_unclaimed_gfis=item.get("active_unclaimed_gfis", 5),
        )
        pr_events = generate_synthetic_pr_events(
            owner=item["owner"],
            name=item["name"],
            p1=pillars[0],
            now=when,
            is_gated=is_gated,
        )
        dd = build_deep_dive(
            meta,
            pr_events,
            when,
            pillar_scores=ps,
            overall_porchlamp=porchlamp,
        )
        dd_str = json.dumps(dd, sort_keys=True)

        eco_str = (
            item["ecosystem"].value
            if hasattr(item["ecosystem"], "value")
            else str(item["ecosystem"])
        )
        legal_str = (
            item["legal_ip_model"].value
            if hasattr(item["legal_ip_model"], "value")
            else str(item["legal_ip_model"])
        )
        ai_str = (
            item["ai_posture"].value
            if hasattr(item["ai_posture"], "value")
            else str(item["ai_posture"])
        )

        rows.append((
            item["owner"],
            item["name"],
            eco_str,
            item.get("domain_category", ""),
            item["stars"],
            item["forks"],
            item["monthly_downloads"],
            item["dependents_count"],
            round(pillars[1] / 100.0, 4),
            item["elephant_factor"],
            legal_str,
            ai_str,
            p_scores[0],
            p_scores[1],
            p_scores[2],
            p_scores[3],
            p_scores[4],
            lev,
            fric,
            quadrant,
            porchlamp,
            tier,
            is_dq,
            dq_reason,
            when_iso,
            dd_str,
        ))

    sql = """
    INSERT INTO repositories (
        owner, name, ecosystem, domain_category, stars, forks,
        monthly_downloads, dependents_count, openssf_criticality,
        elephant_factor, legal_ip_model, ai_posture,
        score_responsiveness, score_criticality, score_governance,
        score_rigor_ai, score_accessibility, leverage_score,
        friction_score, matrix_quadrant, overall_porchlamp, rank_tier,
        is_stage0_disqualified, disqualification_reason, last_indexed_at,
        deep_dive_json
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(owner, name) DO UPDATE SET
        ecosystem = excluded.ecosystem,
        domain_category = excluded.domain_category,
        stars = excluded.stars,
        forks = excluded.forks,
        monthly_downloads = excluded.monthly_downloads,
        dependents_count = excluded.dependents_count,
        openssf_criticality = excluded.openssf_criticality,
        elephant_factor = excluded.elephant_factor,
        legal_ip_model = excluded.legal_ip_model,
        ai_posture = excluded.ai_posture,
        score_responsiveness = excluded.score_responsiveness,
        score_criticality = excluded.score_criticality,
        score_governance = excluded.score_governance,
        score_rigor_ai = excluded.score_rigor_ai,
        score_accessibility = excluded.score_accessibility,
        leverage_score = excluded.leverage_score,
        friction_score = excluded.friction_score,
        matrix_quadrant = excluded.matrix_quadrant,
        overall_porchlamp = excluded.overall_porchlamp,
        rank_tier = excluded.rank_tier,
        is_stage0_disqualified = excluded.is_stage0_disqualified,
        disqualification_reason = excluded.disqualification_reason,
        last_indexed_at = excluded.last_indexed_at,
        deep_dive_json = excluded.deep_dive_json
    """
    with store.connection:
        store.connection.executemany(sql, rows)


def seed_benchmark_store(
    store: OperationalStore,
    count: int = 1000,
    now: Optional[datetime] = None,
) -> int:
    """Score, upsert, and hydrate benchmark repositories into an OperationalStore.

    Returns the number of seeded repositories.
    """
    catalog = get_benchmark_catalog(count)
    seed_benchmark_catalog(store, catalog, now or BENCHMARK_NOW)
    return len(catalog)


def build_benchmark_store(
    db_path: str = ":memory:",
    count: int = 1000,
    now: Optional[datetime] = None,
) -> OperationalStore:
    """Create an OperationalStore and seed it with benchmark repositories."""
    store = OperationalStore(db_path)
    seed_benchmark_store(store, count=count, now=now)
    return store
