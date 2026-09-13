"""W2-14 acceptance: every envelope path recorded in a bundle is relative to
the data root, never absolute.

Fail-before (the real bug, `docs/v2/00-README.md` non-negotiables): the
served evidence endpoint returned a `source` like
`/home/pctablet505/Projects/cvi-scratch/sample-200/raw/gfi/torvalds__linux.json`
-- an absolute path of the machine that derived it. `cvi.derive.bundle`
resolved a v1 document's provenance entry to a real envelope file under
`raw_root`, then recorded `str(path)` verbatim; since `raw_root` (and every
data root in this codebase, `cvi.paths.data_root`) is always absolute, so
was every recorded `source`.

The fix (`cvi.derive.bundle._relative_source`) always expresses the
resolved envelope path relative to the data root before it reaches a
`Measured`/sidecar fact or `ctx.referenced`, and `cvi.core.models.
Measured.__post_init__` (via `measured_problems`) refuses to construct one
with an absolute source at all -- a single enforcement point every
construction site in `cvi.derive.bundle` (v1 resolution and v2 document
loading alike) passes through.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from _fixtures import FETCHED_AT, base_document, prov, write_document, write_envelope

from cvi.core.models import BundleValidationError, feature_provenance_hash
from cvi.derive.bundle import load_bundle

KEY = "acme__widget"


def _write_fixture(root: Path, *, key: str = KEY) -> Path:
    """One minimal v1 document with a single Measured input
    (``elephant_factor``) whose provenance ``source`` is an absolute path of
    an *unrelated, original* machine (`_fixtures.recorded_source`) -- the
    same shape every real sample-200 v1 document carries."""
    write_envelope(root / "raw", "contributors", key, {"count": 3})
    doc = base_document(
        meta={"elephant_factor": 3},
        provenance={"elephant_factor": prov("contributors", key=key)},
        confidence={"elephant_factor": "derived"},
    )
    return write_document(root, doc, key=key)


def test_derive_produces_a_relative_source_for_a_resolved_v1_envelope(tmp_path: Path) -> None:
    path = _write_fixture(tmp_path)
    bundle = load_bundle(path, raw_root=tmp_path / "raw")
    fact = bundle.inputs["elephant_factor"]
    assert fact.source == f"raw/contributors/{KEY}.json"
    assert not fact.source.startswith("/")
    # The referenced-envelope registry (what provenance_hash is computed
    # from, cvi.core.models.feature_provenance_hash) keys by the same
    # relative string -- Measured.source and the hash input never disagree.
    assert bundle.provenance_hash == feature_provenance_hash({fact.source: fact.source_hash})


def test_derive_refuses_an_absolute_source_when_it_cannot_relativise(tmp_path: Path) -> None:
    """No ``raw_root`` is given, so the recorded provenance ``source`` --
    the real, absolute path this envelope was written at -- is the only
    candidate ``_resolve_envelope`` can try (its own documented fallback:
    "otherwise the recorded path is used when it still exists"). It exists
    (this test wrote it there), so resolution succeeds; the absolute path
    it resolved to must still be refused (`BundleValidationError`), never
    recorded as a bundle's provenance as-is -- this is the exact failure
    mode the real, live API served before the fix."""
    key = "acme__widget"
    envelope_path = write_envelope(tmp_path / "raw", "contributors", key, {"count": 3})
    doc = base_document(
        meta={"elephant_factor": 3},
        provenance={"elephant_factor": {"source": str(envelope_path), "fetched_at": FETCHED_AT}},
        confidence={"elephant_factor": "derived"},
    )
    path = write_document(tmp_path, doc, key=key)
    with pytest.raises(BundleValidationError) as excinfo:
        load_bundle(path, raw_root=None)
    assert "absolute" in str(excinfo.value)


def test_relative_provenance_is_identical_across_two_simulated_machines(tmp_path_factory) -> None:
    """The reproducibility property (W2-14): two "machines" holding a copy
    of the same data under different absolute roots produce byte-identical
    provenance -- the same relative ``source`` and the same
    ``provenance_hash``/per-envelope ``source_hash`` -- because neither is a
    function of where the data root happens to sit on disk.
    """
    machine_a = tmp_path_factory.mktemp("machine_a_cvi_data")
    machine_b = tmp_path_factory.mktemp("machine_b_totally_different_data_root")

    path_a = _write_fixture(machine_a)
    path_b = _write_fixture(machine_b)
    assert machine_a != machine_b  # the two roots really are different absolute locations

    bundle_a = load_bundle(path_a, raw_root=machine_a / "raw")
    bundle_b = load_bundle(path_b, raw_root=machine_b / "raw")

    fact_a = bundle_a.inputs["elephant_factor"]
    fact_b = bundle_b.inputs["elephant_factor"]
    assert fact_a.source == fact_b.source == f"raw/contributors/{KEY}.json"
    assert fact_a.source_hash == fact_b.source_hash
    assert bundle_a.provenance_hash == bundle_b.provenance_hash
