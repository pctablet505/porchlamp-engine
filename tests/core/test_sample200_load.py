"""W2-1 acceptance: FeatureBundle v2 loads the on-disk derived JSON.

Design-note requirement: the loader reads the v1 derived document format
(sample-200, produced by the W0-1 sample from ``scripts/derive.py``) and
returns a schema-version-2 FeatureBundle whose provenance is an envelope
path plus hash (R-4). ``source_hash`` is the 03 section 5 content hash
(sha256 of the canonical JSON of the envelope's ``data`` field); the values
pinned below were computed from the frozen sample-200 envelopes. (The
sample-200 manifest's ``sha256`` fields are the W0-1 tool's whole-file byte
hashes - a different fact - so the test does not read that manifest.)

The sample is read-only; the test copies one bundle plus its referenced
envelopes into its own temp dir and loads with ``raw_root`` pointed at the
copy, so the test never reads or writes the live data tree.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from cvi.core import constants_v2
from cvi.core.models import Measured, PRWindowEvent, Unmeasured
from cvi.derive.bundle import load_bundle, write_bundle

SAMPLE = Path.home() / "Projects" / "cvi-scratch" / "sample-200"
KEY = "EvanLi__Github-Ranking"
COLLECTORS = ("contributors", "github_meta", "pr_events", "pr_reviews", "tree")


#: 03 section 5 content hashes of the frozen sample-200 envelopes for
#: EvanLi/Github-Ranking, computed from the envelope data fields.
ENVELOPE_HASHES: dict[str, str] = {
    "contributors": "f99f93d0d2cf108b62e4ba485e186811fc71a116fc2bb1087a0105b382f8d835",
    "github_meta": "8b27b7e399dbf701817141a01d72c432dd22c5d67c75e2891b81436c09b61c8e",
    "pr_events": "9db9d324c7d131ca6069b567fd41031f09aac121964b96f24d93b357d37477af",
    "pr_reviews": "9e50348456702dabbbd088e03c5ee6a4e5b70794fc592dac6fa10e20bd2a8720",
    "tree": "b93b475536eb2acafdba45235e6f1baeb522f94884d563e56f90f460f7812536",
}


@pytest.fixture(scope="module")
def sample(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    if not SAMPLE.is_dir():
        pytest.skip(f"sample-200 not present at {SAMPLE}")
    root = tmp_path_factory.mktemp("w2-1-sample")
    dest = root / "derived"
    dest.mkdir()
    shutil.copy(SAMPLE / "derived" / f"{KEY}.json", dest / f"{KEY}.json")
    raw = root / "raw"
    for collector in COLLECTORS:
        (raw / collector).mkdir(parents=True)
        shutil.copy(SAMPLE / "raw" / collector / f"{KEY}.json", raw / collector / f"{KEY}.json")
    return {"bundle": dest / f"{KEY}.json", "raw": raw}


@pytest.fixture(scope="module")
def bundle(sample: dict[str, Path]) -> Any:
    return load_bundle(sample["bundle"], raw_root=sample["raw"])


def test_repo_identity_values(bundle: Any) -> None:
    repo = bundle.repo
    assert repo.owner == "EvanLi"
    assert repo.name == "Github-Ranking"
    assert repo.ecosystem == "pypi"
    assert repo.archived is False
    assert repo.license_spdx == "MIT"
    assert repo.default_branch == "master"
    assert repo.fork is False
    assert repo.mirror_url is None
    assert bundle.schema_version == 2


def test_inputs_carry_exactly_the_eighteen_ids(bundle: Any) -> None:
    assert set(bundle.inputs) == set(constants_v2.PARAM_INPUT_IDS)
    assert len(bundle.inputs) == 18


def test_measured_inputs_carry_envelope_provenance(bundle: Any, sample: dict[str, Path]) -> None:
    elephant = bundle.inputs["elephant_factor"]
    assert isinstance(elephant, Measured)
    assert elephant.value == 1
    assert elephant.n == 0
    assert elephant.method == "primary"
    assert elephant.window_start is None
    assert elephant.window_end is None
    assert elephant.source == str(sample["raw"] / "contributors" / f"{KEY}.json")
    assert elephant.source_hash == ENVELOPE_HASHES["contributors"]
    assert elephant.fetched_at == "2026-09-12T01:10:41.191668+00:00"

    legal_ip = bundle.inputs["legal_ip"]
    assert isinstance(legal_ip, Measured)
    assert legal_ip.value == "DCO"
    assert legal_ip.source == str(sample["raw"] / "tree" / f"{KEY}.json")
    assert legal_ip.source_hash == ENVELOPE_HASHES["tree"]
    assert legal_ip.fetched_at == "2026-09-12T01:10:44.983176+00:00"


def test_unmeasured_inputs_have_documented_reasons(bundle: Any) -> None:
    assert bundle.inputs["downloads_monthly"] == Unmeasured(
        reason="third_party_gap", detail="registry returned no downloads data"
    )
    assert bundle.inputs["ttfhr_hours"].reason == "collector_failure"
    assert bundle.inputs["dr90_decision_rate"].reason == "collector_failure"
    assert bundle.inputs["dependents_deduped"].reason == "collector_failure"
    assert bundle.inputs["ai_posture"].reason == "collector_failure"
    assert "not an envelope" in bundle.inputs["ai_posture"].detail
    assert bundle.inputs["promotion_ladder"].reason == "collector_failure"


def test_pr_window_values(bundle: Any) -> None:
    window = bundle.pr_window
    assert window.truncated is False
    assert window.start == "2020-02-15T16:51:19+00:00"
    assert window.end == "2026-08-24T05:31:52+00:00"
    assert len(window.events) == 20
    assert window.events[0] == PRWindowEvent(
        pr_number=62,
        created_at="2026-08-24T05:31:52+00:00",
        closed_at=None,
        merged_at=None,
        author_login="3ni8ma",
        author_association="NONE",
        is_external=True,
        first_human_review_at=None,
    )
    assert window.events[1] == PRWindowEvent(
        pr_number=57,
        created_at="2026-03-27T18:12:14+00:00",
        closed_at=None,
        merged_at=None,
        author_login="23f3000015",
        author_association="NONE",
        is_external=True,
        first_human_review_at="2026-04-12T17:00:34+00:00",
    )


def test_cohorts_are_empty_for_v1_data(bundle: Any) -> None:
    assert bundle.cohorts.decision_pr_numbers == ()
    assert bundle.cohorts.newcomer_authors == ()


def test_provenance_hash_matches_independent_recomputation(bundle: Any, sample: dict[str, Path]) -> None:
    """04 section 3: provenance_hash is the hash of all referenced envelope
    hashes. Recomputed here from the pinned content hashes of the five
    referenced envelopes, independently of the loader's hashing code path."""
    raw = sample["raw"]
    referenced = [
        {"source": str(raw / collector / f"{KEY}.json"), "source_hash": digest}
        for collector, digest in ENVELOPE_HASHES.items()
    ]
    payload = json.dumps(referenced, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert bundle.provenance_hash == expected


def test_dump_and_reload_round_trip(bundle: Any, sample: dict[str, Path], tmp_path: Path) -> None:
    out = tmp_path / "repacked.json"
    write_bundle(bundle, out)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema_version"] == 2
    assert set(doc["inputs"]) == set(constants_v2.PARAM_INPUT_IDS)
    assert doc["inputs"]["ttfhr_hours"]["kind"] == "unmeasured"
    assert doc["inputs"]["elephant_factor"]["kind"] == "measured"
    assert doc["inputs"]["elephant_factor"]["source_hash"] == bundle.inputs["elephant_factor"].source_hash
    reloaded = load_bundle(out, raw_root=sample["raw"])
    assert reloaded == bundle
    assert reloaded.provenance_hash == bundle.provenance_hash
