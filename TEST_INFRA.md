# Contributor Value Index (CVI) E2E Test Infrastructure Specification

**Document Version:** 1.0.0  
**Author:** `teamwork_preview_test_writer` (`test_writer_e2e_1`)  
**Workspace:** `/home/pctablet505/Projects/contributor-value-index`  
**Reference Documents:** `ORIGINAL_REQUEST.md`, `PROJECT.md`, `docs/ARCHITECTURE.md`, `docs/UI_UX_SPECIFICATION.md`

---

## 1. Test Architecture Overview

The CVI E2E Test Suite provides an authoritative, opaque-box verification harness ensuring that the Contributor Value Index platform fulfills all user requirements, interface contracts, mathematical invariants, and performance SLAs.

The testing architecture is structured into a four-tier testing hierarchy following the Project Pattern:

```
tests/e2e/
├── conftest.py                   # Shared fixtures (in-memory benchmark store, TestClient, portal thread safety)
├── test_tier1_features.py        # Tier 1: Feature Coverage (>=5 tests per core feature)
│   ├── Benchmark Catalog
│   ├── Canonical Parameter Hierarchy (WAM)
│   ├── Maintainer Improvement Playbook Diagnostics
│   ├── Repository Comparator (Screen E)
│   ├── API Endpoints (/healthz, /matrix, /repos, /leaderboard, /reproject)
│   ├── Dual Theme Engine (Dark #090D16 & Light #F8FAFC)
│   └── Zero-CDN & Offline Delivery
├── test_tier2_boundaries.py      # Tier 2: Boundary Value Analysis & Corner Cases
│   ├── Empty and Malformed Inputs
│   ├── Limit and Offset Boundaries
│   ├── Cardinal Tier Score Cutoffs (89.99 vs 90.00, etc.)
│   ├── Matrix Exact (50.0, 50.0) Coordinate Boundaries
│   ├── Zero PR Events and Undefined Telemetry
│   ├── Persona Weight Sum Constraints & Normalization Tolerances
│   └── Stage 0 Hard Gating Thresholds (0.50 Ghost Rate, 0 Commits)
├── test_tier3_combinations.py    # Tier 3: Pairwise & Cross-Feature Combinations
│   ├── Ecosystem Filter + Persona Projection + Search Pagination
│   ├── Pagination Sequence Invariants & Monotonic Global Ranks
│   ├── Playbook Diagnostics + Tier Upgrade + Comparator Integration
│   ├── Quad-Quadrant Multi-Repo Comparator Matrix
│   ├── Whole-Table Reprojection vs Query Persona Consistency
│   └── Gated Repository State Propagation Across Surfaces
└── test_tier4_scenarios.py       # Tier 4: Real-World Application Workloads & Landmark Ground Truth
    ├── Landmark PyPI: vllm-project/vllm (Sweet Spot, Tier S/S+, sub-12h TTFHR, DCO)
    ├── Landmark Systems: torvalds/linux (Crucible, Off-Platform LKML Routing)
    ├── Landmark PyPI: keras-team/keras-hub (Graveyard, Stage 0 Black Hole, 0.0 CVI)
    ├── Landmark npm: facebook/react (Sweet Spot Anchor, High Dependents & Scale)
    ├── Landmark Go: kubernetes/kubernetes (Crucible/Sweet Spot, CNCF CLA, Multi-Vendor)
    └── Landmark Systems: gentoo/gentoo (AI Policy Posture Ban: BALANCED vs AI_ASSISTED)
```

---

## 2. Methodology

The E2E test suite adheres to rigorous testing methodologies:

1. **Category-Partitioning:**
   System inputs are systematically partitioned into disjoint equivalence classes (e.g. ecosystems, personas, quadrants, tiers, valid/invalid weights, gated/non-gated repositories).
2. **Boundary Value Analysis (BVA):**
   Tests probe extreme boundaries: score cutoffs (e.g. 89.99 vs 90.0, 79.99 vs 80.0), coordinate thresholds (50.00 vs 49.99), pagination limits (0, 1, 1000, 1001), weight sums ($1.0 \pm 10^{-6}$), and Stage-0 gates (50% ghost rate).
3. **Pairwise & Combinatorial Testing:**
   Complex cross-cutting parameters (such as `ecosystem` $\times$ `persona` $\times$ `search` $\times$ `page`) are evaluated together to ensure no unexpected interaction failures or state corruption occurs.
4. **Real-World Application Workloads:**
   Verifies end-to-end behavior against empirical ground-truth repositories representing production ecosystems (PyPI, npm, Crates, Go, Maven, Systems).
5. **Opaque-Box Testing Integrity:**
   All assertions verify observable external behavior (HTTP response codes, JSON bodies, HTML structure, CSS properties, mathematical outputs) through official API endpoints and web interfaces rather than private internal implementation details.

---

## 3. Test Runner Commands

### 3.1 Running the Full E2E Test Suite
```bash
env -u PYTEST_ADDOPTS .venv/bin/python -m pytest tests/e2e/ -v
```

### 3.2 Running Individual Tiers
```bash
# Tier 1: Core Feature Coverage
env -u PYTEST_ADDOPTS .venv/bin/python -m pytest tests/e2e/test_tier1_features.py -v

# Tier 2: Boundary and Corner Cases
env -u PYTEST_ADDOPTS .venv/bin/python -m pytest tests/e2e/test_tier2_boundaries.py -v

# Tier 3: Cross-Feature Combinations
env -u PYTEST_ADDOPTS .venv/bin/python -m pytest tests/e2e/test_tier3_combinations.py -v

# Tier 4: Real-World Landmark Scenarios
env -u PYTEST_ADDOPTS .venv/bin/python -m pytest tests/e2e/test_tier4_scenarios.py -v
```

### 3.3 Running Full Project Suite with Coverage
```bash
env -u PYTEST_ADDOPTS .venv/bin/python -m pytest --cov=cvi --cov-branch --cov-report=term-missing tests/ -n auto -p no:cacheprovider
```

---

## 4. Coverage Thresholds & Pass Criteria

| Tier | Target Scope | Minimum Test Count | Acceptance Criteria |
|---|---|---|---|
| **Tier 1: Feature Coverage** | Benchmark Catalog, WAM Weights, Playbook, Comparator, API, Dual Theme, Zero-CDN | $\ge 5$ tests per feature (min 35 total) | All 7 features validated across standard operational envelopes |
| **Tier 2: Boundary & Corner Cases** | Extreme scores, limits, zero events, precision tolerances, invalid payloads | $\ge 12$ boundary tests | Precise HTTP status codes (404, 422), correct tier assignments, zero division safety |
| **Tier 3: Cross-Feature Combinations** | Multi-parameter filtering, pagination consistency, cross-surface synchronization | $\ge 8$ combination tests | Monotonic ranking, zero duplicate keys, identical projection semantics across endpoints |
| **Tier 4: Landmark Scenarios** | Ground truth for vllm, linux, keras-hub, react, kubernetes, gentoo | $\ge 6$ scenario tests | Pinned tier, quadrant, gating status, and persona divergence matching specification |

---

## 5. Fixture Architecture & Thread Safety

### SQLite Thread-Binding Convention
In FastAPI applications using SQLite (such as `OperationalStore`), connection handles are bound to the operating system thread that created them. Because Starlette's `TestClient` executes ASGI requests within an asynchronous event-loop portal thread, SQLite stores initialized on the main test thread will raise:
```
sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread.
```

To eliminate this defect, all E2E fixtures use Starlette's portal bridge:
```python
@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    app = create_app()
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    test_client = TestClient(app)
    test_client.__enter__()
    # Build in-memory operational store directly on the portal worker thread:
    app.state.store = test_client.portal.call(lambda: build_benchmark_store(count=1000))
    try:
        yield test_client
    finally:
        test_client.__exit__(None, None, None)
```
This guarantees 100% thread safety, sub-millisecond execution times, and zero cross-test state leakage.
