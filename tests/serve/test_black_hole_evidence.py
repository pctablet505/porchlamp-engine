"""Black-hole evidence: the raw-window breakdown and the API's refusal to
show a panel the raw envelope does not support.

black_hole_window_breakdown_from_raw populates counted_prs and excluded
without exposing contributor logins (Privacy & Security). The API headline is
recomputed from the raw envelope at the snapshot clock (never from a params
row: the store writes none for a gated repository); the store-built path is
tests/serve/test_black_hole_evidence_store_built.py.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from porchlamp.derive.gate_evidence import black_hole_window_breakdown_from_raw
from porchlamp.serve.app import create_app
from serve.helpers import SNAPSHOT_ID, finalize, fresh_db


def test_black_hole_evidence_api_and_derivation(tmp_path: Path) -> None:
    db_path = tmp_path / "porchlamp.db"
    conn = fresh_db(db_path, snapshot_id=SNAPSHOT_ID)

    # 1. Insert a black_hole gated repository
    conn.execute(
        """
        INSERT INTO repositories (
            snapshot_id, owner, name, ecosystem, raw_hash, constants_hash,
            eligible, gate_reason, rated, coverage, persona_gates, persona_composites, suspicion
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            SNAPSHOT_ID, "ghost-org", "abandoned-lib", "python", "a" * 64, "c" * 64,
            0, "black_hole", 0, 0.0, "{}", "{}", "[]",
        ),
    )
    # 2. Insert a healthy, rated repository (not a black hole)
    conn.execute(
        """
        INSERT INTO repositories (
            snapshot_id, owner, name, ecosystem, raw_hash, constants_hash,
            eligible, gate_reason, rated, coverage, rank, porchlamp, persona_gates, persona_composites, suspicion
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            SNAPSHOT_ID, "active-org", "healthy-lib", "python", "b" * 64, "c" * 64,
            1, None, 1, 0.95, 1, 88.5, "{}", "{}", "[]",
        ),
    )

    # 3. Write raw pr_window envelope on disk under tmp_path / "raw" / "pr_window"
    raw_root = tmp_path / "raw"
    pr_window_dir = raw_root / "pr_window"
    pr_window_dir.mkdir(parents=True, exist_ok=True)

    window_anchor = "2026-09-01T00:00:00Z"
    prs_data = [
        # Unanswered external PR
        {
            "number": 101,
            "createdAt": "2026-04-01T12:00:00Z",
            "authorAssociation": "NONE",
            "author": {
                "login": "external-alice-secret",
                "__typename": "User",
                "createdAt": "2020-01-01T00:00:00Z",
            },
            "isDraft": False,
            "closedAt": None,
            "mergedAt": None,
            "comments": {"nodes": []},
            "reviews": {"nodes": []},
        },
        # Answered external PR
        {
            "number": 102,
            "createdAt": "2026-04-05T12:00:00Z",
            "authorAssociation": "NONE",
            "author": {
                "login": "external-bob-secret",
                "__typename": "User",
                "createdAt": "2020-01-01T00:00:00Z",
            },
            "isDraft": False,
            "closedAt": None,
            "mergedAt": None,
            "comments": {
                "nodes": [
                    {
                        "createdAt": "2026-04-06T12:00:00Z",
                        "authorAssociation": "MEMBER",
                        "author": {
                            "login": "maintainer-carol-secret",
                            "__typename": "User",
                        },
                        "bodyText": "Thanks for submitting this pull request! We will review it shortly.",
                    }
                ]
            },
            "reviews": {"nodes": []},
        },
        # Excluded draft PR
        {
            "number": 103,
            "createdAt": "2026-04-10T12:00:00Z",
            "authorAssociation": "NONE",
            "author": {
                "login": "external-dan-secret",
                "__typename": "User",
                "createdAt": "2020-01-01T00:00:00Z",
            },
            "isDraft": True,
            "readyForReviewAt": None,
            "closedAt": None,
            "mergedAt": None,
            "comments": {"nodes": []},
            "reviews": {"nodes": []},
        },
        # Excluded author self-closed PR
        {
            "number": 104,
            "createdAt": "2026-04-12T12:00:00Z",
            "authorAssociation": "NONE",
            "author": {
                "login": "external-eve-secret",
                "__typename": "User",
                "createdAt": "2020-01-01T00:00:00Z",
            },
            "isDraft": False,
            "closedAt": "2026-04-13T12:00:00Z",
            "closedByAuthor": True,
            "mergedAt": None,
            "comments": {"nodes": []},
            "reviews": {"nodes": []},
        },
    ]

    envelope_payload = {
        "fetched_at": "2026-09-01T01:00:00Z",
        "data": {
            "window_anchor": window_anchor,
            "window_days": 180,
            "prs": prs_data,
        },
    }
    (pr_window_dir / "ghost-org__abandoned-lib.json").write_text(
        json.dumps(envelope_payload), encoding="utf-8"
    )

    conn.commit()
    finalize(conn, db_path, snapshot_id=SNAPSHOT_ID)
    conn.close()

    # Verify black_hole_window_breakdown_from_raw directly
    ref_now = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
    breakdown = black_hole_window_breakdown_from_raw(
        raw_root, "ghost-org__abandoned-lib", now=ref_now
    )
    assert breakdown is not None
    assert breakdown.unanswered == 1
    assert breakdown.population_n == 2
    assert breakdown.excluded_draft == 1
    assert breakdown.excluded_self_closed == 1
    assert len(breakdown.counted_prs) == 2
    counted_numbers = [pr.number for pr in breakdown.counted_prs]
    assert 101 in counted_numbers
    assert 102 in counted_numbers

    # Verify API response
    client = TestClient(create_app(db_path, web_dir=tmp_path))

    # Gated black-hole repo
    resp = client.get("/v1/porchlamp/repos/ghost-org/abandoned-lib")
    assert resp.status_code == 200
    data = resp.json()
    assert data["gate_reason"] == "black_hole"
    # The raw window says 1 of 2 unanswered: not a black hole, so the API
    # shows no panel rather than one that contradicts the stored verdict.
    assert data["black_hole_evidence"] is None

    # Non-black-hole repo returns None for black_hole_evidence
    resp2 = client.get("/v1/porchlamp/repos/active-org/healthy-lib")
    assert resp2.status_code == 200
    assert resp2.json()["black_hole_evidence"] is None
