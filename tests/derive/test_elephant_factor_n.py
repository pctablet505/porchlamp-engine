"""3.2 elephant factor carries its real sample size end to end.

``scripts/derive.py`` records the number of commits the factor was computed
from (non-bot, org-attributable, inside the trailing 365 d) as
``elephant_factor_commit_count``; ``porchlamp.derive.bundle`` passes it as
``Measured.n``; the parameter scorer then applies the 30-commit minimum
(``constants.PARAM_MIN_N["elephant_factor"]``). Before this, ``n`` was always
0 and every repo read ``insufficient_n``.

The oracle counts below are by hand from the fixture rows, not from the code
under test.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

from _fixtures import load

from porchlamp.core import constants
from porchlamp.core.models import Measured
from porchlamp.core.params import elephant_factor as score_elephant_factor

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("derive_ef_n", ROOT / "scripts" / "derive.py")
derive = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(derive)

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)
OWNER, NAME, KEY = "acme", "widget", "acme__widget"
IN_WINDOW = "2026-08-01T00:00:00Z"
OUT_OF_WINDOW = "2025-08-01T00:00:00Z"  # 407 d before NOW: outside 365 d


def _row(login: str, email: str, date: str = IN_WINDOW, author_type: str = "User") -> dict:
    return {"date": date, "author_email": email, "author_login": login,
            "author_type": author_type, "signed_off": False}


def _rows(n_corp_a: int, n_corp_b: int) -> list[dict]:
    """``n_corp_a`` + ``n_corp_b`` attributable human commits, plus rows the
    sample must NOT count: 7 bot commits (three D-IN-1 bot rules), 3 with no
    email (no org), and 4 outside the 365 d window."""
    rows = [_row("alice", "alice@a-corp.com") for _ in range(n_corp_a)]
    rows += [_row("bob", "bob@b-corp.com") for _ in range(n_corp_b)]
    rows += [_row("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com",
                  author_type="Bot") for _ in range(3)]
    rows += [_row("renovate", "bot@renovateapp.com", author_type="Bot") for _ in range(2)]
    rows += [_row("", "41898282+github-actions[bot]@users.noreply.github.com") for _ in range(2)]
    rows += [_row("carol", "") for _ in range(3)]
    rows += [_row("dave", "dave@d-corp.com", date=OUT_OF_WINDOW) for _ in range(4)]
    return rows


def _derive_and_load(tmp_path: Path, rows: list[dict]):
    envelope = tmp_path / "raw" / "commits_365d" / f"{KEY}.json"
    envelope.parent.mkdir(parents=True, exist_ok=True)
    envelope.write_text(json.dumps({"fetched_at": "2026-09-11T00:00:00Z", "source": "test",
                                    "data": {"count": len(rows), "cap": 1000, "truncated": False,
                                             "rows": rows}}), encoding="utf-8")
    doc = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path, fetch=False, now=NOW)
    return doc, load(tmp_path, json.loads(json.dumps(doc)))


def test_measured_with_true_n_at_or_above_min_n(tmp_path: Path) -> None:
    # 20 a-corp + 16 b-corp = 36 attributable human commits; a-corp alone is
    # 20/36 = 55.6% >= 50%, so EF = 1.
    doc, bundle = _derive_and_load(tmp_path, _rows(20, 16))
    assert doc["meta"]["elephant_factor"] == 1
    assert doc["meta"]["elephant_factor_commit_count"] == 36
    assert doc["confidence"]["elephant_factor_commit_count"] == "derived"
    value = bundle.inputs["elephant_factor"]
    assert isinstance(value, Measured)
    assert (value.value, value.n) == (1, 36)
    assert value.source == f"raw/commits_365d/{KEY}.json"
    scored = score_elephant_factor(value)
    assert scored.measured is True
    assert scored.n == 36
    assert scored.score == 25.0  # min(100, 25 * EF)


def test_insufficient_n_below_min_n_reports_true_n(tmp_path: Path) -> None:
    # 10 + 9 = 19 attributable human commits (< 30); the 7 bot, 3 email-less
    # and 4 out-of-window rows would lift it to 33 if any were counted.
    assert constants.PARAM_MIN_N["elephant_factor"] == 30
    doc, bundle = _derive_and_load(tmp_path, _rows(10, 9))
    assert doc["meta"]["elephant_factor"] == 1
    value = bundle.inputs["elephant_factor"]
    assert isinstance(value, Measured)
    assert value.n == 19
    scored = score_elephant_factor(value)
    assert scored.measured is False
    assert scored.reason == "insufficient_n"
    assert scored.n == 19
    assert scored.score is None


def test_sample_excludes_bots_and_matches_the_factor_denominator(tmp_path: Path) -> None:
    # 15 + 15 = 30 humans: exactly min n. Counting the 7 bot commits would
    # give 37. One org reaches exactly 50% (15/30), so EF = 1.
    doc, bundle = _derive_and_load(tmp_path, _rows(15, 15))
    assert doc["meta"]["elephant_factor_commit_count"] == 30
    assert doc["meta"]["elephant_factor"] == 1
    scored = score_elephant_factor(bundle.inputs["elephant_factor"])
    assert scored.measured is True and scored.n == 30


def test_contributors_only_fallback_keeps_n_zero(tmp_path: Path) -> None:
    """Without a commits_365d envelope the v1 contributors approximation
    still sets ``elephant_factor`` but has no commit sample: n stays 0, so
    it scores ``insufficient_n`` exactly as before (no over-correction)."""
    envelope = tmp_path / "raw" / "contributors" / f"{KEY}.json"
    envelope.parent.mkdir(parents=True, exist_ok=True)
    envelope.write_text(json.dumps({"fetched_at": "2026-09-11T00:00:00Z", "source": "test",
                                    "data": [{"login": "a", "contributions": 100}]}),
                        encoding="utf-8")
    doc = derive.derive_repo(OWNER, NAME, derive.Ecosystem.PYPI, tmp_path, fetch=False, now=NOW)
    assert "elephant_factor_commit_count" not in doc["meta"]
    bundle = load(tmp_path, json.loads(json.dumps(doc)))
    value = bundle.inputs["elephant_factor"]
    assert isinstance(value, Measured) and value.n == 0
    assert score_elephant_factor(value).reason == "insufficient_n"
