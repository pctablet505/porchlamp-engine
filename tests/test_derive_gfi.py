"""W1-1 derivation of active_unclaimed_gfis from per-label GFI envelopes.

Separate file by lead grant: the shared tests/test_derive.py is owned by
other packets that append to it; this file loads scripts/derive.py the same
way and asserts the new W1-1 gfi block plus the legacy-path guard.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("derive", ROOT / "scripts" / "derive.py")
derive = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(derive)

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)
OWNER, NAME = "acme", "widget"
KEY = f"{OWNER}__{NAME}"


def seed_gfi(tmp: Path, data: Any, at: str = "2026-09-12T00:00:00Z") -> None:
    p = tmp / "raw" / "gfi" / f"{KEY}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"fetched_at": at, "source": "github-rest:test",
                             "data": data}), encoding="utf-8")


def run(tmp: Path) -> dict:
    return derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp,
                              fetch=False, now=NOW)


def new_data(labels_matched: list[str], issues: list[dict],
             truncated: bool = False) -> list[dict]:
    """W1-1 gfi envelope data: metadata record first, then issue records."""
    return [{"gfi_newcomer_v2": True, "labels_matched": labels_matched,
             "queries": [f"repos/{OWNER}/{NAME}/issues?state=open&labels={label}"
                         for label in labels_matched],
             "truncated": truncated}, *issues]


def test_new_format_counts_open_unclaimed_recent_matched(tmp_path: Path):
    """Exact count: closed, assigned and 91-day-old recorded issues are
    excluded; an issue matched only via a per-repo alias counts; the legacy
    hyphen-spelled labels no longer gate the count (5.1's alias regex does).
    """
    seed_gfi(tmp_path, new_data(["good first issue", "E-easy"], [
        {"number": 100, "state": "open", "assignee": None,
         "updated_at": "2026-09-07T00:00:00Z", "labels": ["E-easy"]},
        {"number": 101, "state": "closed", "assignee": None,
         "updated_at": "2026-09-10T00:00:00Z", "labels": ["good-first-issue"]},
        {"number": 102, "state": "open", "assignee": "bob",
         "updated_at": "2026-09-10T00:00:00Z", "labels": ["good first issue"]},
        {"number": 103, "state": "open", "assignee": None,
         "updated_at": "2026-06-13T00:00:00Z", "labels": ["good first issue"]},
        {"number": 104, "state": "open", "assignee": None,
         "updated_at": "2026-09-02T00:00:00Z",
         "labels": ["good first issue", "E-easy"]},
    ]))
    out = run(tmp_path)
    m, c, prov = out["meta"], out["confidence"], out["provenance"]
    assert m["active_unclaimed_gfis"] == 2
    assert c["active_unclaimed_gfis"] == "verified"
    assert prov["active_unclaimed_gfis"]["source"].endswith(f"raw/gfi/{KEY}.json")
    assert prov["active_unclaimed_gfis"]["fetched_at"] == "2026-09-12T00:00:00Z"


def test_window_anchored_at_envelope_fetched_at(tmp_path: Path):
    """The 90-day window anchors at the envelope's fetched_at, so the count
    is reproducible from recorded data (the 2026-02-01 issue is 29 days old
    at fetch time but far older than the derive clock of 2026-09-12)."""
    seed_gfi(tmp_path, new_data(["good first issue"], [
        {"number": 201, "state": "open", "assignee": None,
         "updated_at": "2026-02-01T00:00:00Z", "labels": ["good first issue"]},
    ]), at="2026-03-01T00:00:00Z")
    out = run(tmp_path)
    assert out["meta"]["active_unclaimed_gfis"] == 1


def test_no_matching_labels_is_measured_zero(tmp_path: Path):
    """A repo with no alias-matching labels is a measured zero (D-MISS-1:
    repo-controlled absence), not a missing field."""
    seed_gfi(tmp_path, new_data([], []))
    out = run(tmp_path)
    assert out["meta"]["active_unclaimed_gfis"] == 0
    assert out["confidence"]["active_unclaimed_gfis"] == "verified"


def test_legacy_list_keeps_old_derivation(tmp_path: Path):
    """Over-correction guard: pre-W1-1 list-shaped gfi envelopes keep the
    legacy derivation (30d window, hyphen labels) untouched."""
    seed_gfi(tmp_path, [
        {"number": 10, "assignee": None, "updated_at": "2026-09-07T00:00:00Z",
         "labels": ["good-first-issue"]},
        {"number": 11, "assignee": None, "updated_at": "2026-09-08T00:00:00Z",
         "labels": [{"name": "help-wanted"}]},
        {"number": 12, "assignee": "bob", "updated_at": "2026-09-07T00:00:00Z",
         "labels": ["good-first-issue"]},
        {"number": 13, "assignee": None, "updated_at": "2026-07-01T00:00:00Z",
         "labels": ["good-first-issue"]},
    ])
    out = run(tmp_path)
    assert out["meta"]["active_unclaimed_gfis"] == 2
    assert out["confidence"]["active_unclaimed_gfis"] == "verified"


def test_absent_gfi_envelope_stays_missing(tmp_path: Path):
    out = run(tmp_path)
    assert out["meta"]["active_unclaimed_gfis"] == 0
    assert out["confidence"]["active_unclaimed_gfis"] == "missing"
