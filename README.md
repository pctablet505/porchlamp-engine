# Contributor Value Index (CVI) Platform
### Empirical Analytics Engine & 2D Discovery Matrix for Open-Source Software Contributors

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Status: Architectural Design](https://img.shields.io/badge/Status-Design%20%26%20Planning-emerald.svg)](#)
[![Diagrams: Pure Mermaid](https://img.shields.io/badge/Diagrams-Pure%20Mermaid-indigo.svg)](#)
[![Assets: Scalable Vector SVG](https://img.shields.io/badge/Assets-Vector%20SVG-purple.svg)](#)

---

## Executive Overview

The **Contributor Value Index (CVI) Platform** is an empirical analytics engine and discovery platform that evaluates open-source software (OSS) repositories worldwide based on **contributor return-on-investment (ROI)**.

Rather than relying on uncalibrated vanity metrics (stars, raw downloads), the platform models the trade-off between:
- **Contributor Leverage ($Y$-axis):** Long-term career capital, real-world production footprint, architectural rigor, and downstream dependency entrenchment.
- **Contributor Friction ($X$-axis):** Effective human review latency, PR ghosting rates, single-organization corporate gatekeeping (*CHAOSS Elephant Factor*), legal CLA exposure, and toxic maintainer interactions.

---

## Visual Platform Interfaces (Vector Design Assets)

### 1. The 2D Contributor Discovery Matrix & Scorecard Drawer
*Interactive spatial canvas decomposing Contributor Leverage ($Y$) vs. Contributor Friction ($X$) into four operational quadrants.*

![CVI Platform 2D Discovery Matrix](docs/assets/cvi_platform_matrix.svg)

### 2. Multi-Ecosystem Ranked Leaderboard & Filter Toolbar
*High-density tabular ranking across programming language ecosystems with sparklines, tier badges, and Stage 0 black hole warnings.*

![CVI Multi-Ecosystem Leaderboard](docs/assets/cvi_leaderboard.svg)

### 3. Custom Priority & Dynamic Re-Ranking Configurator
*Interactive 5-pillar weighting sliders with lock-and-balance logic and real-time split-screen rank shift deltas (`▲ +14`, `▼ -4`).*

![CVI Custom Priority Configurator](docs/assets/cvi_custom_ranking.svg)

### 4. Deep-Dive Repository Scorecard Drawer
*Slide-over inspection drawer with 5-pillar radar polygon, Kaplan-Meier response latency survival curve, CHAOSS Elephant Factor breakdown, and active unclaimed GFIs.*

![CVI Scorecard Drawer](docs/assets/cvi_scorecard_drawer.svg)

---

## The 2D Contributor Discovery Matrix

Repositories are evaluated across two orthogonal dimensions and classified into four operational quadrants:

```mermaid
flowchart TB
    subgraph Matrix ["2D Contributor Discovery Matrix"]
        direction TB
        subgraph TopRow ["High Contributor Leverage: Impact, Prestige and Rigor"]
            direction LR
            Crucible["The Crucible (High Barrier, Elite Return)<br/>e.g. torvalds/linux, chromium/chromium<br/>• Foundational Systems Entrenchment<br/>• Strict Subsystem Gatekeepers / Patch Workflows"]
            SweetSpot["The Sweet Spot (Fast Reviews, Huge Impact)<br/>e.g. vllm-project/vllm, duckdb/duckdb<br/>• Verified Multi-Vendor Governance<br/>• High First-Timer Merge and 48h Response"]
        end
        subgraph BottomRow ["Low Contributor Leverage: Peripheral or Blocked"]
            direction LR
            Graveyard["The Graveyard (PR Black Hole, Low ROI)<br/>e.g. keras-team/keras-hub<br/>• Single-Vendor Corporate Silo<br/>• External PRs Ghosted (over 50% Ghost Rate)"]
            Nursery["The Nursery (Safe Starter PRs, Welcoming)<br/>e.g. docs hubs, beginner-friendly devtools<br/>• Pro-Social Onboarding and Curated GFIs<br/>• High Merge Velocity for First PRs"]
        end
    end
    Crucible -. "Lower Friction" .-> SweetSpot
    Graveyard -. "Lower Friction" .-> Nursery
    Graveyard -. "Higher Leverage" .-> Crucible
    Nursery -. "Higher Leverage" .-> SweetSpot
```

---

## The 5-Phase Ranking Pipeline

```mermaid
flowchart TB
    Input["Target Repository Candidate"] --> Phase0{"Phase 0: Elimination-by-Aspects (EBA)<br/>1. Active non-bot commit in trailing 180 days?<br/>2. External PR Ghost Rate GR_14 <= 50%?<br/>3. Platform-native review workflow present?<br/>4. Persona constraints satisfied (e.g. AI-Ban)?"}
    
    Phase0 -- "Fails Gate" --> Relegated["Relegated / Specialized Routing<br/>• Black Hole Flag: Capped at Tier D/C<br/>• Off-Platform Flag: Routed to Specialized Category"]
    
    Phase0 -- "Passes Gate" --> Phase1["Phase 1: Ingestion and Anti-Gaming De-Biasing<br/>• Strip automated CI bots and service accounts<br/>• StarScout audit: remove throwaway accounts and burst rings<br/>• Dependency sanitization: count unique root-org dependents"]

    Phase1 --> Phase2["Phase 2: Within-Ecosystem ECDF Normalization<br/>• Map raw signals to [0, 100] percentiles per ecosystem<br/>• Neutralize npm (fine-grained) vs Crates/Go (monolithic) skew"]

    Phase2 --> P1
    Phase2 --> P2
    Phase2 --> P3
    Phase2 --> P4
    Phase2 --> P5

    subgraph Phase3 ["Phase 3: Multi-Pillar Scoring Engine (WAM)"]
        direction TB
        P1["Pillar 1: Responsiveness and Review Lifecycle (Weight: 25%)"]
        P2["Pillar 2: Production Criticality and Impact (Weight: 25%)"]
        P3["Pillar 3: Governance, Safety and Path-to-Power (Weight: 20%)"]
        P4["Pillar 4: Engineering Rigor and AI Alignment (Weight: 15%)"]
        P5["Pillar 5: Accessibility, DX and Newcomer On-Ramps (Weight: 15%)"]
    end

    P1 --> Phase4["Phase 4: 2D Matrix Coordinate Decomposition<br/>• Leverage = f(Criticality, Rigor, Governance)<br/>• Friction = f(TTFHR, Ghost Rate, Elephant Factor, CLA)"]
    P2 --> Phase4
    P3 --> Phase4
    P4 --> Phase4
    P5 --> Phase4

    Phase4 --> Phase5["Phase 5: Persona-Adaptive Leaderboard Projection<br/>• Balanced Default, First-Timer, Career Capital, Senior Systems, AI-Assisted"]

    Phase5 --> Output["Final CVI Score (0 - 100), Quadrant and Verified Tier"]
```

---

## The 5 Evaluation Pillars (15 Empirical Parameters)

| Evaluation Pillar & Weight | Validated Parameters & Parameter Weights | Operational Definition & Scope |
| :--- | :--- | :--- |
| **Pillar 1: Responsiveness & Review Lifecycle (25%)** | • 1.1 Bot-Filtered Human TTFHR (35%)<br/>• 1.2 First-Timer Merge Rate MR_first (25%)<br/>• 1.3 PR Ghost Rate GR_14 (20%)<br/>• 1.4 PR Merge Latency Tail Risk TTM_p90 (20%) | Median response latency, external merge velocity, 14-day abandonment ceiling, and 90th percentile merge tail risk. |
| **Pillar 2: Production Criticality & Impact (25%)** | • 2.1 Downstream Registry Dependents (40%)<br/>• 2.2 Canonical OpenSSF Criticality Score (35%)<br/>• 2.3 Filtered Active Monthly Downloads (25%) | Real-world software ecosystem entrenchment, Rob Pike multi-signal criticality, and installer-filtered download volume. |
| **Pillar 3: Governance, Safety & Path-to-Power (20%)** | • 3.1 Legal IP Regime & Relicensing Safety (35%)<br/>• 3.2 CHAOSS Elephant Factor / Decentralization (35%)<br/>• 3.3 Contributor Promotion Ladder (30%) | IP protection against corporate relicensing traps (DCO vs. CLA), organizational decentralization, and non-sponsor maintainer pathways. |
| **Pillar 4: Engineering Rigor & AI Alignment (15%)** | • 4.1 OpenSSF Scorecard Pipeline Defenses (40%)<br/>• 4.2 Calibrated AI Policy Posture (35%)<br/>• 4.3 Review Depth & Substantive Guidance (25%) | Automated branch defenses and testing rigor, transparent AI policy governance, and balanced substantive inline code feedback. |
| **Pillar 5: Accessibility, DX & Newcomer On-Ramps (15%)** | • 5.1 Freshness of Unclaimed GFIs (40%)<br/>• 5.2 90-Day Newcomer Return Rate RR_90 (35%)<br/>• 5.3 Developer Experience & Setup Friction (25%) | Active unclaimed Good First Issues (< 30d), newcomer retention velocity, and reproducible devcontainer/CI execution runtime. |

---

## Comprehensive Documentation

- **[Architecture Specification (HLD & LLD)](docs/ARCHITECTURE.md):** Complete formal data contracts, Rob Pike canonical OpenSSF criticality procedural algorithms, ECDF normalization, StarScout fraud filters, DuckDB analytical lakehouse design, REST/GraphQL interface specifications, and verification test matrices.
- **[UI/UX Architecture & Design System](docs/UI_UX_SPECIFICATION.md):** Design tokens, 4 archetypal user personas, screen-by-screen layouts, interactive state machines, and WCAG 2.1 AA accessibility standards.

---

## License

This project is licensed under the Apache 2.0 License. See [LICENSE](LICENSE) for details.
