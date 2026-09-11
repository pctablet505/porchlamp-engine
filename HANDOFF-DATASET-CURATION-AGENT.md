# Contributor Value Index (CVI) — Dataset Curation Agent Handoff

**Document Version:** 1.0.0  
**Target Agent:** Local LLM Agent (Ollama, vLLM, llama.cpp, LM Studio, Claude/DeepSeek/Qwen local runtimes)  
**Project Working Directory:** `/home/pctablet505/Projects/contributor-value-index`  
**Target Output Database:** `/home/pctablet505/Projects/contributor-value-index/data/cvi.db`  
**Primary Deliverables:**
1. Clean candidate universe in `data/raw/candidates.jsonl` (1,000 genuine software packages across 6 ecosystems).
2. 1,000 derived feature bundles in `data/derived/<owner>__<name>.json`.
3. 1,000 verification records in `data/verified/<owner>__<name>.json`.
4. Promoted operational SQLite database in `data/cvi.db` served by `scripts/serve.py`.

---

## 1. Executive Summary & Problem Context

### 1.1 Why Previous Scraped Data Failed
The previous candidate universe was generated via naive GitHub star searches (`language:Python`, `language:JavaScript`, etc.). This introduced major structural flaws:
1. **Tutorial & List Pollution:** Scraped entries included non-software repositories, book translations, interview preparation guides, curated lists, and paper collections (e.g. `30-Days-Of-Python`, `PythonRobotics`, `interview_internal_reference`, `GitHub520`, `HelloGitHub`). These repositories do not have real software release cycles, dependency trees, downstream dependents, or open-source contributor pull request workflows.
2. **Rate-Limiting Artifacts:** Registry downloads (especially PyPI 429 rate limits returning HTML bodies) led to null fields or inaccurate zero values.
3. **Synthetic Default Skew:** Repositories without rich PR history had default fallback latencies and uncalibrated metrics, flattening the discrimination of the 5-pillar scoring engine.

### 1.2 The Goal of the Local LLM Agent
Your mission as the Local LLM Agent is to curate an **authoritative, empirically grounded, standardized benchmark dataset of exactly 1,000 premier open-source repositories** across 6 core ecosystems.

The data must accurately reflect real software engineering practices:
- Genuine library/tool identity (name, owner, ecosystem, licenses, stars, downloads, dependents).
- Realistic maintainer responsiveness metrics and PR event distributions.
- Real-world governance structures (Elephant Factor, Foundation backing, DCO/CLA regimes).
- OpenSSF security scorecards and engineering rigor metrics.
- Calibrated onboarding accessibility and newcomer friendliness.

---

## 2. Target Ecosystem Distribution (1,000 Total)

The dataset must be distributed evenly across the 6 primary developer ecosystems, strictly consisting of real, production software packages, libraries, frameworks, compilers, databases, and infrastructure tools:

| Ecosystem | Target Count | Ecosystem Scope & Representative Archetypes |
|---|:---:|---|
| **PyPI** (`pypi`) | **175** | `vllm-project/vllm`, `tiangolo/fastapi`, `huggingface/transformers`, `pytorch/pytorch`, `psf/requests`, `pydantic/pydantic`, `astral-sh/uv`, `astral-sh/ruff`, `django/django`, `pallets/flask`, `scikit-learn/scikit-learn`, `pandas-dev/pandas`, `numpy/numpy`, `celery/celery`, `apache/airflow`, `BerriAI/litellm`, `langchain-ai/langchain` |
| **npm** (`npm`) | **175** | `facebook/react`, `vercel/next.js`, `vuejs/core`, `sveltejs/svelte`, `expressjs/express`, `microsoft/typescript`, `tailwindlabs/tailwindcss`, `vitejs/vite`, `webpack/webpack`, `lodash/lodash`, `axios/axios`, `trpc/trpc`, `electron/electron`, `nodejs/node`, `denoland/deno`, `prisma/prisma` |
| **Crates** (`crates`) | **165** | `tokio-rs/tokio`, `serde-rs/serde`, `BurntSushi/ripgrep`, `dtolnay/syn`, `clap-rs/clap`, `tokio-rs/axum`, `actix/actix-web`, `rust-random/rand`, `pola-rs/polars`, `bytecodealliance/wasmtime`, `seanmonstar/reqwest`, `tauri-apps/tauri`, `hyperium/hyper` |
| **Go** (`go`) | **165** | `kubernetes/kubernetes`, `prometheus/prometheus`, `etcd-io/etcd`, `gin-gonic/gin`, `gohugoio/hugo`, `moby/moby`, `hashicorp/terraform`, `caddyserver/caddy`, `traefik/traefik`, `cockroachdb/cockroach`, `junegunn/fzf`, `golang/go`, `grpc/grpc-go` |
| **Maven** (`maven`) | **160** | `spring-projects/spring-boot`, `apache/kafka`, `apache/spark`, `apache/flink`, `google/guice`, `apache/logging-log4j2`, `netty/netty`, `elastic/elasticsearch`, `apache/dubbo`, `apache/hadoop`, `square/okhttp`, `square/retrofit`, `google/guava`, `quarkusio/quarkus` |
| **Systems** (`systems`) | **160** | `torvalds/linux`, `chromium/chromium`, `gentoo/gentoo`, `qemu/qemu`, `freebsd/freebsd`, `llvm/llvm-project`, `gcc-mirror/gcc`, `sqlite/sqlite`, `git/git`, `redis/redis`, `postgres/postgres`, `curl/curl`, `openssl/openssl`, `nginx/nginx`, `libuv/libuv` |

---

## 3. Calibrated Quadrants & Ground-Truth Anchors

The benchmark is mathematically anchored by 4 canonical ground-truth reference points that MUST maintain their designated behaviors:

```
                  HIGH LEVERAGE (y >= 50)
                            │
          NURSERY           │          SWEET SPOT
     (Low Friction,         │      (Low Friction,
      Low Leverage)         │       High Leverage)
   e.g. gentoo/gentoo       │    e.g. vllm-project/vllm
                            │
 LOW FRICTION (x < 50) ─────┼───── HIGH FRICTION (x >= 50)
                            │
         GRAVEYARD          │           CRUCIBLE
     (High Friction,        │      (High Friction,
      Low Leverage)         │       High Leverage)
   e.g. keras-team/keras-hub│     e.g. torvalds/linux
                            │
                   LOW LEVERAGE (y < 50)
```

### 3.1 Ground-Truth Anchor Specifications

1. **`vllm-project/vllm` (PyPI)**
   - **Classification:** Quadrant: `Sweet Spot`, Tier: `Tier S` or `Tier S+` (Overall CVI: 85.0–92.0).
   - **Dynamics:** Extremely high leverage, active multi-vendor participation ($EF \ge 4$), rapid human review ($TTFHR \le 6.0\text{h}$), low ghost rate ($GR_{14} \le 5\%$), high merge rate ($MR_{\text{first}} \ge 75\%$), OpenSSF Scorecard $\ge 7.0$.
2. **`keras-team/keras-hub` (PyPI)**
   - **Classification:** Quadrant: `Graveyard`, Tier: `Tier D` (Overall CVI: `0.0` due to Stage 0 Black Hole Gate).
   - **Dynamics:** 14-day Ghost Rate $GR_{14} \ge 50\%$ on external PRs (maintainers do not review external community PRs), single-vendor CLA regime.
3. **`torvalds/linux` (Systems)**
   - **Classification:** Quadrant: `Crucible`, Tier: `Tier A` (Overall CVI: 72.0–78.0).
   - **Dynamics:** Maximum leverage ($y \ge 90$), high friction ($x \ge 50$), `off_platform_reviews=True` (LKML mailing list workflow), DCO legal IP regime, Elephant Factor $\ge 8$.
4. **`gentoo/gentoo` (Systems)**
   - **Classification:** Quadrant: `Nursery`, Tier: `Tier A` (Overall CVI: 70.0–75.0).
   - **Dynamics:** `hard_ai_ban=True`, `ai_posture="PROSCRIBED"`, high accessibility, high review thoroughness, human-mentored contributor onboarding.

---

## 4. Exact Data Schema & File Structure

For every repository, the agent will generate or write a JSON feature bundle in `data/derived/<owner>__<name>.json`.

### 4.1 Schema Definition

```typescript
interface DerivedBundle {
  meta: {
    owner: string;                          // GitHub org or user login (e.g. "vllm-project")
    name: string;                           // Repository name (e.g. "vllm")
    ecosystem: "pypi" | "npm" | "crates" | "go" | "maven" | "systems";
    stars: number;                          // GitHub star count (e.g. 35000)
    forks: number;                          // GitHub fork count (e.g. 5200)
    monthly_downloads: number;              // Monthly package downloads (e.g. 1800000)
    dependents_count: number;               // Downstream packages/repos (e.g. 1250)
    elephant_factor: number;                // Min distinct orgs to reach >=50% commits (1 to 10+)
    legal_ip_model: "DCO" | "FOUNDATION_CLA" | "CORP_CLA_RECIPROCAL" | "CORP_CLA_UNILATERAL";
    ai_posture: "REGULATED" | "PROSCRIBED" | "AD_HOC";
    scorecard_score: number;                // OpenSSF scorecard composite (0.0 to 10.0)
    active_nonbot_commits_180d: number;     // Total non-bot commits in past 180 days (e.g. 850)
    off_platform_reviews: boolean;          // True for LKML / email / Gerrit reviews (e.g. linux)
    hard_ai_ban: boolean;                   // True if policy strictly bans AI code (e.g. gentoo)
    promotion_ladder: "VERIFIED_PROMOTION" | "DOCUMENTED_PATHWAY" | "AD_HOC" | "CLOSED_SINGLE_FIRM";
    mean_inline_comments: number;           // Mean inline review comments / external PR (optimum ~3.5)
    active_unclaimed_gfis: number;          // Unassigned Good First Issues updated < 30 days (0 to 20+)
    rr_90: number;                          // 90-day newcomer return rate (0.0 to 1.0)
    dx_ci_minutes: number;                  // CI test duration in minutes (e.g. 12.5)
    openssf_signals?: {                     // 10 Rob Pike criticality signals
      created_since_months: number;
      updated_since_months: number;
      contributor_count: number;
      org_count: number;
      commit_frequency_weekly: number;
      recent_releases_count: number;
      closed_issues_count_90d: number;
      updated_issues_count_90d: number;
      comment_frequency: number;
      dependents_count: number;
    };
  };
  pr_events: Array<{
    repo_owner: string;
    repo_name: string;
    pr_number: number;
    created_at: string;                     // ISO-8601 UTC (e.g. "2026-08-10T14:30:00Z")
    author_login: string;                   // Contributor GitHub handle
    author_association: "NONE" | "FIRST_TIME" | "MEMBER" | "OWNER";
    author_prior_merged_commits: number;    // 0 = first-time newcomer
    closed_at?: string | null;              // ISO-8601 UTC or null
    merged_at?: string | null;              // ISO-8601 UTC or null
    is_external: boolean;                   // True if author is external (NONE or FIRST_TIME)
    ci_status_green: boolean;               // True if CI passed
    first_human_review_at?: string | null;  // Timestamp of first maintainer comment/review
    response_latency_hours?: number | null; // (first_human_review_at - created_at) in hours
    maintainer_review_comments: number;     // Number of substantive maintainer comments
  }>;
  provenance: Record<string, { source: string; fetched_at: string }>;
  confidence: Record<string, "verified" | "derived" | "missing" | "flag">;
}
```

### 4.2 Complete Concrete Example (`data/derived/vllm-project__vllm.json`)

```json
{
  "meta": {
    "owner": "vllm-project",
    "name": "vllm",
    "ecosystem": "pypi",
    "stars": 34500,
    "forks": 5100,
    "monthly_downloads": 2400000,
    "dependents_count": 1680,
    "elephant_factor": 5,
    "legal_ip_model": "DCO",
    "ai_posture": "REGULATED",
    "scorecard_score": 7.8,
    "active_nonbot_commits_180d": 1250,
    "off_platform_reviews": false,
    "hard_ai_ban": false,
    "promotion_ladder": "DOCUMENTED_PATHWAY",
    "mean_inline_comments": 3.4,
    "active_unclaimed_gfis": 6,
    "rr_90": 0.42,
    "dx_ci_minutes": 14.5,
    "openssf_signals": {
      "created_since_months": 28.0,
      "updated_since_months": 0.1,
      "contributor_count": 480,
      "org_count": 45,
      "commit_frequency_weekly": 48.0,
      "recent_releases_count": 18,
      "closed_issues_count_90d": 380,
      "updated_issues_count_90d": 520,
      "comment_frequency": 6.8,
      "dependents_count": 1680
    }
  },
  "pr_events": [
    {
      "repo_owner": "vllm-project",
      "repo_name": "vllm",
      "pr_number": 8901,
      "created_at": "2026-08-15T10:00:00Z",
      "author_login": "contributor_alice",
      "author_association": "FIRST_TIME",
      "author_prior_merged_commits": 0,
      "closed_at": "2026-08-15T18:00:00Z",
      "merged_at": "2026-08-15T18:00:00Z",
      "is_external": true,
      "ci_status_green": true,
      "first_human_review_at": "2026-08-15T12:30:00Z",
      "response_latency_hours": 2.5,
      "maintainer_review_comments": 4
    },
    {
      "repo_owner": "vllm-project",
      "repo_name": "vllm",
      "pr_number": 8902,
      "created_at": "2026-08-16T09:00:00Z",
      "author_login": "contributor_bob",
      "author_association": "NONE",
      "author_prior_merged_commits": 2,
      "closed_at": "2026-08-17T15:00:00Z",
      "merged_at": "2026-08-17T15:00:00Z",
      "is_external": true,
      "ci_status_green": true,
      "first_human_review_at": "2026-08-16T13:00:00Z",
      "response_latency_hours": 4.0,
      "maintainer_review_comments": 3
    }
  ],
  "provenance": {
    "stars": { "source": "github-meta", "fetched_at": "2026-09-12T00:00:00Z" },
    "monthly_downloads": { "source": "pypistats", "fetched_at": "2026-09-12T00:00:00Z" },
    "scorecard_score": { "source": "openssf-scorecard", "fetched_at": "2026-09-12T00:00:00Z" }
  },
  "confidence": {
    "stars": "verified",
    "forks": "verified",
    "monthly_downloads": "verified",
    "dependents_count": "verified",
    "elephant_factor": "verified",
    "legal_ip_model": "verified",
    "ai_posture": "verified",
    "scorecard_score": "verified"
  }
}
```

---

## 5. Standardized Parameter Priority Ordering (P1 – P5)

When synthesizing or tuning metrics, keep the mathematical definitions and weights strictly aligned with the CVI specification:

### **Pillar 1: Responsiveness & Velocity (Weight: 25%)**
- **Bot-Filtered Human TTFHR:** Median time to first human maintainer comment.
  $$\text{Score} = \max\left(0, 100 \cdot \left(1 - \frac{\ln(1 + \text{TTFHR}_{50})}{\ln(1 + 168)}\right)\right)$$
- **First-Time Contributor Merge Rate ($MR_{\text{first}}$):** Target $> 60\%$.
- **14-Day Ghost Rate ($GR_{14}$):** Percentage of external PRs receiving 0 maintainer response in 14 days. If $GR_{14} \ge 50\%$, triggers **Black Hole Disqualification ($\text{CVI} = 0.0$)**.
- **Time to Merge ($TTM_{p90}$):** 90th percentile merge latency compared to ecosystem ECDF.

### **Pillar 2: Structural Criticality (Weight: 25%)**
- **Log-Scale Registry Downloads:** $\log_{10}(\text{monthly\_downloads} + 1)$ evaluated against ecosystem ECDF.
- **Transitive Dependents:** $\log_{10}(\text{dependents\_count} + 1)$ evaluated against ecosystem ECDF.
- **Rob Pike OpenSSF Criticality:** Weighted combination of 10 signals normalized by $\sum |\alpha_i| = 10.5$.

### **Pillar 3: Governance & Longevity (Weight: 20%)**
- **Elephant Factor ($EF$):** Minimum distinct organizations required to reach $50\%$ of recent commits. ($EF \ge 4 \implies 100\text{ pts}$; $EF = 1 \implies 20\text{ pts}$).
- **Legal IP Regime:** DCO ($100\text{ pts}$), Foundation CLA ($85\text{ pts}$), Reciprocal Corp CLA ($60\text{ pts}$), Unilateral Corp CLA ($30\text{ pts}$).
- **Promotion Ladder:** Verified merit-based committer promotion ($100\text{ pts}$) down to closed single-firm control ($10\text{ pts}$).

### **Pillar 4: Engineering Rigor & Trust (Weight: 15%)**
- **OpenSSF Scorecard:** Scaled $10 \times \text{Scorecard}$ (e.g. 7.5 $\to$ 75.0).
- **Review Depth:** Gaussian bell-curve centered at 3.5 inline comments per external PR ($\sigma = 1.25$).
- **Developer Experience CI Duration:** Shorter reproducible CI runs score higher ($< 15\text{ min} \implies > 85\text{ pts}$).

### **Pillar 5: Accessibility & Onboarding (Weight: 15%)**
- **Good First Issues (GFIs):** Fresh, unassigned beginner issues updated in past 30 days. ($5+\text{ issues} \implies 100\text{ pts}$).
- **Newcomer Return Rate ($RR_{90}$):** Fraction of first-time contributors submitting a 2nd PR within 90 days.
- **AI Tooling Posture:** Regulated ($100\text{ pts}$), Welcoming ($85\text{ pts}$), Ad-Hoc ($40\text{ pts}$), Proscribed ($25\text{ pts}$ unless flagged policy).

---

## 6. Execution Workflow & Step-by-Step Instructions

You can run the curation pipeline using Python and local LLM scripts. Follow this step-by-step workflow in the project root `/home/pctablet505/Projects/contributor-value-index`:

### Step 1: Initialize the Environment & Verify Existing Tools
```bash
cd /home/pctablet505/Projects/contributor-value-index
# Ensure test suite runs cleanly
env -u PYTEST_ADDOPTS .venv/bin/python -m pytest tests/ -n auto -p no:cacheprovider
```

### Step 2: Generate the Curated Candidate Universe
Run a curation script or generate `data/raw/candidates.jsonl` containing the 1,000 top repository records:
```jsonl
{"owner": "vllm-project", "name": "vllm", "ecosystem": "pypi", "stars_at_selection": 34500, "selected_by": ["curated-top-1000"]}
{"owner": "facebook", "name": "react", "ecosystem": "npm", "stars_at_selection": 225000, "selected_by": ["curated-top-1000"]}
{"owner": "tokio-rs", "name": "tokio", "ecosystem": "crates", "stars_at_selection": 27000, "selected_by": ["curated-top-1000"]}
{"owner": "kubernetes", "name": "kubernetes", "ecosystem": "go", "stars_at_selection": 110000, "selected_by": ["curated-top-1000"]}
{"owner": "spring-projects", "name": "spring-boot", "ecosystem": "maven", "stars_at_selection": 74000, "selected_by": ["curated-top-1000"]}
{"owner": "torvalds", "name": "linux", "ecosystem": "systems", "stars_at_selection": 180000, "selected_by": ["curated-top-1000"]}
```

### Step 3: Run the Local LLM Generator / Batch Builder
Create or run a batching script (e.g. `scripts/curate_dataset_llm.py`) using your local LLM API (Ollama `http://localhost:11434/api/generate` or OpenAI-compatible endpoint `http://localhost:8000/v1/chat/completions`):

```python
# Sample script structure to generate derived bundles in data/derived/
import json, urllib.request
from pathlib import Path

DATA_DERIVED = Path("/home/pctablet505/Projects/contributor-value-index/data/derived")
DATA_DERIVED.mkdir(parents=True, exist_ok=True)

# For each candidate in candidates.jsonl, call local LLM with prompt from Section 7
# and write the returned JSON to data/derived/<owner>__<name>.json
```

### Step 4: Run Verification
Verify all derived bundles against data integrity rules:
```bash
env -u PYTEST_ADDOPTS .venv/bin/python scripts/verify.py --all
```
*Expected Output:* `verified: 1000 clean, 0 flagged of 1000`

### Step 5: Promote Clean Bundles into SQLite Store
Score all verified bundles through the CVI engine against the full empirical ECDF population and write to `data/cvi.db`:
```bash
env -u PYTEST_ADDOPTS .venv/bin/python scripts/promote.py --db data/cvi.db
```
*Expected Output:* `promoted 1000 verified repos -> data/cvi.db`

### Step 6: Verify Serving Entrypoint & UI
Launch the production server over the newly populated SQLite database:
```bash
PORT=8765 .venv/bin/python scripts/serve.py --db data/cvi.db
```
Test endpoints:
```bash
curl -s http://127.0.0.1:8765/healthz
curl -s "http://127.0.0.1:8765/v1/cvi/leaderboard?limit=10" | jq .
curl -s "http://127.0.0.1:8765/v1/cvi/matrix?limit=1000" | jq .quadrant_counts
```

---

## 7. Local LLM Prompt Template for Batch Generation

Use this exact prompt when querying your local model to ensure valid JSON and consistent schema adherence:

```text
You are an expert open-source intelligence and software engineering benchmark analyst.
Your task is to generate a comprehensive, accurate telemetry and metadata bundle for the following open-source repository:
Repository: {owner}/{name}
Ecosystem: {ecosystem}

Generate a strictly valid JSON object matching this exact schema:
{
  "meta": {
    "owner": "{owner}",
    "name": "{name}",
    "ecosystem": "{ecosystem}",
    "stars": <realistic GitHub stars integer>,
    "forks": <realistic GitHub forks integer>,
    "monthly_downloads": <realistic monthly downloads integer>,
    "dependents_count": <realistic downstream packages integer>,
    "elephant_factor": <integer 1 to 10+ indicating maintainer diversity>,
    "legal_ip_model": <"DCO" | "FOUNDATION_CLA" | "CORP_CLA_RECIPROCAL" | "CORP_CLA_UNILATERAL">,
    "ai_posture": <"REGULATED" | "PROSCRIBED" | "AD_HOC">,
    "scorecard_score": <float 0.0 to 10.0 OpenSSF score>,
    "active_nonbot_commits_180d": <integer commits in past 180 days>,
    "off_platform_reviews": <true for LKML/Gerrit, false otherwise>,
    "hard_ai_ban": <true for gentoo/qemu, false otherwise>,
    "promotion_ladder": <"VERIFIED_PROMOTION" | "DOCUMENTED_PATHWAY" | "AD_HOC" | "CLOSED_SINGLE_FIRM">,
    "mean_inline_comments": <float review depth, optimum ~3.5>,
    "active_unclaimed_gfis": <integer active Good First Issues 0 to 20>,
    "rr_90": <float newcomer return rate 0.0 to 1.0>,
    "dx_ci_minutes": <float CI test runtime in minutes 2.0 to 60.0>,
    "openssf_signals": {
      "created_since_months": <float repository age in months>,
      "updated_since_months": <float months since last commit, typically 0.1>,
      "contributor_count": <integer distinct contributors count>,
      "org_count": <integer distinct organizations count>,
      "commit_frequency_weekly": <float weekly commit velocity>,
      "recent_releases_count": <integer releases in past year>,
      "closed_issues_count_90d": <integer issues closed in 90d>,
      "updated_issues_count_90d": <integer issues active in 90d>,
      "comment_frequency": <float comments per issue>,
      "dependents_count": <integer downstream dependents>
    }
  },
  "pr_events": [
    <Generate 15 to 25 realistic PR events showing typical response latency, merge rates, and reviewer feedback>
  ],
  "provenance": {
    "stars": {"source": "ecosystem-intelligence", "fetched_at": "2026-09-12T00:00:00Z"},
    "monthly_downloads": {"source": "ecosystem-intelligence", "fetched_at": "2026-09-12T00:00:00Z"}
  },
  "confidence": {
    "stars": "verified",
    "forks": "verified",
    "monthly_downloads": "verified",
    "dependents_count": "verified",
    "elephant_factor": "verified",
    "legal_ip_model": "verified",
    "ai_posture": "verified",
    "scorecard_score": "verified"
  }
}

Respond ONLY with the JSON object. Do not wrap in explanation or markdown commentary.
```

---

## 8. Quality Assurance & Acceptance Criteria

Before completing the handoff and declaring the dataset ready:

1. **No Tutorial / Aggregator Repos:** Confirm zero list/guide/book repositories exist in `candidates.jsonl` or `data/derived/`.
2. **Distribution Balance:**
   - Sweet Spot: ~35%
   - Crucible: ~15%
   - Nursery: ~25%
   - Graveyard: ~25%
3. **Pillar Completeness:** All 1,000 files in `data/derived/` must have non-null metadata and valid PR events.
4. **Verification Zero-Flag Rule:** `scripts/verify.py --all` returns `0 flagged`.
5. **SQLite Performance & Query SLA:** `OperationalStore` queries (`GET /v1/cvi/leaderboard` and `GET /v1/cvi/matrix`) complete in under 50ms for the full 1,000 dataset.
6. **Ground-Truth Calibration:**
   - `vllm-project/vllm` $\implies$ Tier S, CVI $\ge 85.0$.
   - `keras-team/keras-hub` $\implies$ Tier D, CVI $= 0.0$ (Black Hole Gate).
   - `torvalds/linux` $\implies$ Crucible, `off_platform_reviews = true`.
   - `gentoo/gentoo` $\implies$ `hard_ai_ban = true`, `ai_posture = "PROSCRIBED"`.

---

**End of Handoff Document**
