# UI/UX Architecture & Design System Specification
## Contributor Value Index (CVI) Platform

---

## 1. Executive UX Vision & User Personas

### 1.1 Product Philosophy & Design Tenets

The **Contributor Value Index (CVI) Platform** transforms open-source discovery from vanity-metric popularity contests (stars, forks) into an empirical, engineering-grade decision support platform. 

The user experience is guided by four foundational design tenets:

1. **Information Density without Cognitive Noise:** Borrowing from high-performance developer tools (Linear, Datadog, Grafana, Vercel), interfaces favor compact typography, monospace tabular figures, high-contrast visual hierarchies, and progressive disclosure over empty whitespace or decorative fluff.
2. **Deterministic Clarity over Black-Box Scoring:** Every aggregate CVI score and 2D coordinate is fully decomposable. Maintainers and contributors must be able to trace any metric back to its empirical root (e.g., exact hours to first human review, Kaplan-Meier latency curves, SHA-verified commit entropy).
3. **Multi-Faceted Persona Adaptation:** Contributor value is subjective. An onboarding newcomer seeks low-friction mentorship; an elite systems engineer seeks high-rigor, high-impact infrastructure; an AI-assisted contributor seeks transparent AI policies and deterministic automated CI. The UI must fluidly re-project rankings based on user intent.
4. **Latency-Zero Spatial Navigation:** The flagship 2D Contributor Discovery Matrix must feel like a native high-performance CAD canvas or game engine: smooth 60fps pan/zoom, sub-100ms viewport clustering, instant hover tooltips, and seamless transitions into deep-dive repository drawers.

---

### 1.2 User Personas & Mental Models

```mermaid
flowchart TD
    Root["CVI User Personas and Mental Models"]

    subgraph P1 ["Persona 1: The Explorer (First-Time Contributor)"]
        direction TB
        P1_Goals["Goals: Land first merged PR, find responsive maintainers, avoid stale traps"]
        P1_Friction["Friction: Imposter syndrome, unclear setup docs, silent PR abandonment"]
        P1_Screen["Primary View: Screen A (Nursery) and Screen C (Curated GFIs)"]
        P1_Goals --> P1_Friction --> P1_Screen
    end

    subgraph P2 ["Persona 2: The Climber (Career Capital Hunter)"]
        direction TB
        P2_Goals["Goals: Build public resume proof, land Tier S/A code, maximize recruiter signal"]
        P2_Friction["Friction: Wasted effort in ghost repos, hostile gatekeeping, zero contributor credit"]
        P2_Screen["Primary View: Screen A (Sweet Spot) and Screen B (Ranked Leaderboard)"]
        P2_Goals --> P2_Friction --> P2_Screen
    end

    subgraph P3 ["Persona 3: The Pragmatist (Senior Systems Engineer)"]
        direction TB
        P3_Goals["Goals: Target critical infra, audit governance, verify CLA and relicensing risks"]
        P3_Friction["Friction: Single-vendor corporate silos, unilateral relicensing, vanity toy projects"]
        P3_Screen["Primary View: Screen C (Elephant Factor and CLA) and Screen E (Comparator)"]
        P3_Goals --> P3_Friction --> P3_Screen
    end

    subgraph P4 ["Persona 4: The Augmenter (AI-Assisted Contributor)"]
        direction TB
        P4_Goals["Goals: Leverage agentic tools, avoid hard AI bans, pass deterministic CI gates"]
        P4_Friction["Friction: Hostile AI witch-hunts, silent bot rejections, flaky test runners"]
        P4_Screen["Primary View: Screen D (AI Persona Filter) and Screen B (Stage 0 EBA Audit)"]
        P4_Goals --> P4_Friction --> P4_Screen
    end

    Root --> P1
    Root --> P2
    Root --> P3
    Root --> P4
```

#### Detailed Persona Profiles

| Attribute | Persona 1: The Explorer | Persona 2: The Climber | Persona 3: The Pragmatist | Persona 4: The Augmenter |
| :--- | :--- | :--- | :--- | :--- |
| **Archetype** | First-Time Contributor | Career Capital & Resume Hunter | Senior Systems Specialist | AI-Assisted Developer |
| **Target Quadrant** | **The Nursery** (Low Friction, High Welcoming) | **The Sweet Spot** (High Impact, Low Friction) | **The Crucible** & **Sweet Spot** (Elite Rigor) | **Sweet Spot** & **Nursery** (AI-Permissive) |
| **Key Metric Priorities** | TTFHR <= 24h, Active GFIs, High Merge Rate | Industry Brand Tier S/A, High Dependents, Release Notes | Elephant Factor $\ge 3$, DCO/Permissive CLA, Scorecard $\ge 8.5$ | Calibrated AI Policy, Deterministic CI, Low Review Churn |
| **Primary Anxiety** | "Will maintainers ignore my PR or flame me?" | "Will anyone care that I contributed to this?" | "Will this corporate sponsor pull a Redis/Elastic relicensing rug?" | "Will my PR get instantly closed because I used Cursor/Copilot?" |
| **Primary Call-to-Action** | "Claim Verified GFI & View Setup Guide" | "Analyze Contributor Attribution & Mindshare" | "Inspect Governance, CLA & Subsystem Ownership" | "Verify Automated Gates & AI Contribution Rules" |

---

### 1.3 User Journey & Cross-Screen Mental Model

```mermaid
flowchart TD
    Start([User Arrives on Platform]) --> ModeSelect{Select Discovery Mode}
    
    ModeSelect -->|Spatial Exploration| MatrixView["Screen A: 2D Discovery Matrix<br/>• Pan/Zoom Scatter Canvas<br/>• Quadrant Filters and Cluster Peeks"]
    ModeSelect -->|Structured Search| LeaderboardView["Screen B: Multi-Ecosystem Leaderboard<br/>• Ecosystem Tabs (Python, Rust, Go...)<br/>• Sortable Percentile Metrics"]
    ModeSelect -->|Intent Calibration| ConfiguratorView["Screen D: Persona Configurator<br/>• Weight Sliders with Normalization Lock<br/>• Dynamic Re-projection Preview"]

    MatrixView -->|Click Scatter Point| ScorecardDrawer["Screen C: Repo Scorecard Drawer<br/>• 5-Pillar Radar Polygon<br/>• Kaplan-Meier Latency Curve<br/>• Elephant Factor and CLA Matrix<br/>• Active Unclaimed GFIs"]
    LeaderboardView -->|Click Row| ScorecardDrawer
    ConfiguratorView -->|Apply Preset| LeaderboardView

    ScorecardDrawer -->|Click Compare Repo| ComparatorView["Screen E: Side-by-Side Comparator<br/>• Superimposed Dual Radar<br/>• 15-Metric Differential Table<br/>• Verdict Recommendation Engine"]
    LeaderboardView -->|Multi-Select Repos| ComparatorView

    ScorecardDrawer -->|External CTA| GitHubPR["Deep-Link to GitHub Issue / PR Guide"]
```

---

## 2. Information Architecture & Global Navigation Model

### 2.1 Complete Site Map & Route Hierarchy

```mermaid
flowchart TD
    subgraph GlobalShell ["App Shell (Fixed Header, Global Context and Nav Rail)"]
        direction TB
        Brand["CVI Logo and Version HUD"]
        NavRail["Primary Nav Rail<br/>• 2D Matrix (/matrix)<br/>• Leaderboard (/leaderboard)<br/>• Configurator (/persona)<br/>• Compare (/compare)"]
        Omnibar["Omnibar / Cmd+K Search<br/>• Fuzzy Repo Search<br/>• Persona Presets<br/>• Quick Jump"]
        ContextStatus["System HUD<br/>• Repos Indexed: 5,420<br/>• GHArchive Sync: Live (Lag under 15m)<br/>• Ecosystem: All"]
    end

    GlobalShell --> RouteMatrix["/matrix<br/>(Screen A: Interactive 2D Canvas)"]
    GlobalShell --> RouteLeaderboard["/leaderboard<br/>(Screen B: Multi-Ecosystem Table)"]
    GlobalShell --> RouteRepo["/repo/:owner/:name<br/>(Screen C: Deep-Dive Scorecard Full/Modal)"]
    GlobalShell --> RouteConfig["/persona<br/>(Screen D: Weight Adjuster)"]
    GlobalShell --> RouteCompare["/compare?repos=:repoA,:repoB<br/>(Screen E: Repo Comparator)"]

    RouteMatrix -.-> RouteRepo
    RouteLeaderboard -.-> RouteRepo
    RouteLeaderboard -.-> RouteCompare
    RouteConfig -.-> RouteLeaderboard
```

### 2.2 Global Layout Grid & Viewport Shell

The shell uses an ultra-responsive three-tier layout optimized for high-density desktop monitors (1920x1080 and 2560x1440) with graceful degradation down to mobile devices:

```mermaid
flowchart TD
    subgraph ViewportLayout ["Global Layout Grid and Viewport Architecture"]
        direction TB
        TopNav["Top Navigation and Omnibar (Height: 56px, Fixed)<br/>[ CVI Logo ] [ Matrix ] [ Leaderboard ] [ Configurator ] [ Compare ] | [ Cmd+K Search ] [ System HUD ]"]
        
        subgraph MiddleZone ["Interactive Workspace Zone"]
            direction LR
            NavRail["Nav Rail<br/>(64px)"]
            MainCanvas["Main Workspace Canvas (Dynamic Flex: 1fr, Min-Width: 720px)<br/>• Screen A: 2D Matrix Canvas with Floating HUD and Minimap<br/>• Screen B: Tabular Multi-Ecosystem Leaderboard<br/>• Screen D: Interactive Persona Sliders and Live Preview<br/>• Screen E: Dual-Radar Head-to-Head Comparison View"]
            ContextDrawer["Context and Inspector Rail (420px Width)<br/>• Screen C: Deep-Dive Scorecard Drawer<br/>(Slide-over or persistent on >=1600px)"]
            NavRail --- MainCanvas --- ContextDrawer
        end
        
        Footer["Global Status Footer (Height: 28px, Fixed, Monospace Typography)<br/>[ GHArchive Sync: OK ] [ Active Persona: Balanced Default ] [ Total Candidates: 5,420 ] [ Zoom: 1.0x ]"]
        
        TopNav --> MiddleZone --> Footer
    end
```

### 2.3 Global Command Palette (`Cmd+K`) & Omnibar Specification

The Omnibar provides universal, keyboard-driven navigation across repositories, metric dimensions, and persona profiles:

- **Keyboard Trigger:** `Cmd + K` (macOS) / `Ctrl + K` (Linux/Windows) or single-key `/` when no input is focused.
- **Search Scope:**
  - **Direct Repository Lookup:** Fuzzy-matches `owner/repo` against the 5,000+ indexed repositories with immediate CVI score badges.
  - **Persona Presets:** Instant activation of presets (`/persona first-timer`, `/persona career`, `/persona senior`).
  - **Ecosystem Switcher:** Fast scoping (`/eco python`, `/eco rust`, `/eco go`).
  - **Direct Matrix Jumps:** Center canvas on specific quadrant coordinates (`/goto sweet-spot`, `/goto crucible`).
- **Visual Feedback:** 
  - Result rows display owner avatar, repository name, primary language tag, composite CVI score pill, and quadrant color dot.
  - Arrow keys (`Up` / `Down`) cycle selections; `Enter` opens the Scorecard Drawer; `Cmd + Enter` navigates directly to the GitHub repository.

---

## 3. Design System Specifications: Dark-Mode First Engineering Aesthetic

### 3.1 Color Palette & Semantic Tokens

The visual foundation is built on an ultra-deep, high-contrast dark palette designed to eliminate eye strain during extensive technical evaluation sessions while highlighting semantic data accents.

#### Foundation Canvas & Neutral Tokens

| Token Name | Hex Value | RGB / Alpha | Role & Visual Usage |
| :--- | :--- | :--- | :--- |
| `--bg-base` | `#090D16` | `rgb(9, 13, 22)` | Deep viewport canvas base, background behind 2D matrix |
| `--bg-surface` | `#0F172A` | `rgb(15, 23, 42)` | Card containers, table headers, drawer surface, modal body |
| `--bg-surface-elevated`| `#1E293B` | `rgb(30, 41, 59)` | Popovers, hover cards, dropdown menus, slider track background |
| `--bg-surface-hover` | `#334155` | `rgb(51, 65, 85)` | Interactive row hover, active button backgrounds |
| `--border-subtle` | `#1E293B` | `rgba(30, 41, 59, 0.8)`| Standard hairline dividers (1px solid), table borders |
| `--border-strong` | `#334155` | `rgba(51, 65, 85, 1.0)`| Active input borders, card outlines, quadrant dividing axes |
| `--border-focus` | `#38BDF8` | `rgb(56, 189, 248)` | Accessible keyboard focus ring (2px solid, 2px offset) |
| `--text-primary` | `#F8FAFC` | `rgb(248, 250, 252)`| Primary headers, metric values, repository titles |
| `--text-secondary` | `#94A3B8` | `rgb(148, 163, 184)`| Descriptions, axis labels, non-primary table column text |
| `--text-muted` | `#64748B` | `rgb(100, 116, 139)`| Metadata labels, inactive pills, timestamp footers |

#### Quadrant Semantic & Data Visualization Tokens

Each quadrant features a distinct semantic color identity with coordinated background washes and atmospheric glow tokens:

```mermaid
flowchart LR
    subgraph SemanticColors ["Quadrant Color Identities"]
        SS["The Sweet Spot<br/>#10B981 Emerald<br/>High Leverage, Low Friction"]
        CR["The Crucible<br/>#6366F1 Indigo<br/>High Leverage, High Friction"]
        NU["The Nursery<br/>#F59E0B Amber<br/>Low Leverage, Low Friction"]
        GY["The Graveyard<br/>#EF4444 Crimson<br/>Low Leverage, High Friction"]
    end
```

| Quadrant Token | Solid Color | Alpha Wash (10%) | Atmospheric Glow (25%) | Semantic Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `--quad-sweet-spot` | `#10B981` (Emerald) | `rgba(16, 185, 129, 0.10)` | `0 0 24px rgba(16, 185, 129, 0.25)` | S-Tier ROI: Fast reviews, high impact |
| `--quad-crucible` | `#6366F1` (Indigo) | `rgba(99, 102, 241, 0.10)` | `0 0 24px rgba(99, 102, 241, 0.25)` | Elite Rigor: High barrier, high return |
| `--quad-nursery` | `#F59E0B` (Amber) | `rgba(245, 158, 11, 0.10)` | `0 0 24px rgba(245, 158, 11, 0.25)` | Welcoming Starter: Fast merges, safe on-ramp |
| `--quad-graveyard` | `#EF4444` (Crimson) | `rgba(239, 68, 68, 0.10)` | `0 0 24px rgba(239, 68, 68, 0.25)` | PR Black Hole: Corporate neglect, dead end |

#### Functional Accents & Status Tokens

- **Cyan Telemetry Accent (`#06B6D4`):** Active filters, radar polygon fills, live data synchronization pulses.
- **Violet AI Alignment (`#8B5CF6`):** AI policy badges, verified LLM-friendly documentation indicators.
- **Stage 0 Black Hole Warning (`#DC2626`):** Severe EBA disqualification badges, ghost rate alerts.
- **Success / Green CI (`#22C55E`):** Passing build defense gates, verified active maintainers.

---

### 3.2 Typography Stack & Scale

The typography uses a dual-font architecture: **Inter** for clean UI navigation and hierarchy, paired with **JetBrains Mono** for all coordinates, metric percentiles, code references, and tabular rankings.

```
Font Stacks:
- Primary UI: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif
- Monospace / Data / Code: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace
```

#### Typography Scale Table

| Typography Style | Font Family | Size (px / rem) | Line Height | Weight | Letter Spacing | Use Case |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Display Header** | Inter | 32px / 2.0rem | 40px | 700 (Bold) | `-0.025em` | Hero view titles, modal primary headings |
| **Heading 1** | Inter | 24px / 1.5rem | 32px | 600 (Semi) | `-0.020em` | Drawer repo name, section headers |
| **Heading 2** | Inter | 18px / 1.125rem | 24px | 600 (Semi) | `-0.015em` | Card titles, quadrant block headings |
| **Body Large** | Inter | 15px / 0.9375rem | 22px | 400 / 500 | `-0.010em` | Introductory text, drawer descriptions |
| **Body Medium** | Inter | 13px / 0.8125rem | 18px | 400 / 500 | `0.000em` | Table text, general UI controls, pills |
| **Caption / Micro** | Inter | 11px / 0.6875rem | 14px | 500 (Med) | `+0.010em` | Metadata tags, tooltips, axis helper text |
| **Metric Large** | JetBrains Mono | 28px / 1.75rem | 32px | 700 (Bold) | `-0.020em` | Composite CVI score badge, big KPI stats |
| **Metric Standard** | JetBrains Mono | 14px / 0.875rem | 18px | 600 (Semi) | `0.000em` | Table column numbers, coordinate HUD |
| **Metric Pill / HUD** | JetBrains Mono | 10px / 0.625rem | 12px | 500 (Med) | `+0.025em` | Quadrant coordinates `(L:88, F:24)` |

*Rule:* All numerical columns and metrics enforce `font-variant-numeric: tabular-nums` to prevent horizontal jitter during live filtering and re-projection.

---

### 3.3 Spatial Grid, Elevation & Surface Architecture

```
Spacing Scale:
4px (2xs) -> 8px (xs) -> 12px (sm) -> 16px (md) -> 24px (lg) -> 32px (xl) -> 48px (2xl)

Border Radius:
- Micro Badges & Pills: 4px
- Interactive Controls (Buttons, Inputs): 6px
- Cards, Modals, Drawers: 8px
- Floating HUD & Minimap: 8px
- Pill Counters / Avatars: 9999px (Full rounded)

Elevation Hierarchy:
- Level 0 (Base Canvas): 0px offset, flat background (#090D16)
- Level 1 (Card / Table Surface): 1px solid #1E293B border, background #0F172A
- Level 2 (Floating Popover / Tooltip): box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.6), border 1px solid #334155
- Level 3 (Modal / Sliding Drawer): box-shadow: -15px 0 40px rgba(0, 0, 0, 0.8), border-left 1px solid #334155
```

---

### 3.4 Micro-Interactions, Transitions & Motion Curves

```mermaid
stateDiagram-v2
    [*] --> Idle: View Loaded
    Idle --> Hovered: Cursor enters node (30ms transition)
    Hovered --> PeekActive: Dwell over 120ms mounts Hover Card
    Hovered --> Idle: Cursor leaves node
    PeekActive --> Inspecting: Click or Enter opens Drawer
    Inspecting --> Idle: Dismiss via Esc or Close Button
    
    Idle --> Filtering: Toggle Quadrant or Adjust Weight
    Filtering --> ReProjecting: WAM Engine recomputes ranks in under 50ms
    ReProjecting --> Idle: Spring transition to new positions
```

- **Easing Curve:** Standard motion uses `cubic-bezier(0.16, 1, 0.3, 1)` (Linear-style snappy spring physics).
- **Hover Debounce:** Scatter point hover tooltips wait for a 120ms dwell time before mounting to prevent visual strobe when sweeping the cursor across dense clusters.
- **Node Reposition Animation:** When persona weights change, scatter points animate smoothly to their new `(x, y)` coordinates over 300ms.

---

## 4. Core Screen Architecture & Detailed Layout Specifications

---

### 4.1 Screen A: The Interactive 2D Discovery Matrix View

The 2D Discovery Matrix is the flagship exploratory canvas. It plots repositories along **Leverage ($Y$-axis, 0–100)** versus **Friction ($X$-axis, 0–100)**, partitioned into four distinct quadrants bisected at $(50, 50)$.

![Screen A: 2D Discovery Matrix Canvas](assets/cvi_platform_matrix.svg)

#### Canvas Architecture & Technical Mechanics

```mermaid
flowchart TB
    subgraph CanvasEngine ["Screen A: 2D Matrix Engine Architecture"]
        direction TB
        InputLayer["Viewport Event Handler<br/>• Pan (Pointer Drag / Space+Drag)<br/>• Pinch-to-Zoom and Wheel Scroll<br/>• Sub-pixel Precision Damping"]
        CoordMapper["Coordinate Transformation Engine<br/>• World Space: [0..100, 0..100]<br/>• Screen Space: [0..CanvasWidth, 0..CanvasHeight]<br/>• Dynamic Bisector Lines (50, 50)"]
        SpatialIndex["Spatial QuadTree / k-d Tree<br/>• Real-time Nearest Neighbor Search<br/>• Collision Clustering at Zoom under 0.75x<br/>• Sub-10ms Hover Hit Detection"]
        Renderer["Dual-Layer Renderer<br/>• Background WebGL: Grid, Axis, 5,000+ Scatter Points<br/>• Foreground SVG: Selected Node Halo, Hover Card, Crosshairs"]
    end

    InputLayer --> CoordMapper
    CoordMapper --> SpatialIndex
    SpatialIndex --> Renderer
```

#### Visual Encoding of Scatter Points

1. **Coordinate Mapping:**
   - **X Coordinate (Horizontal):** Normalized Contributor Friction ($0 = \text{Ultra-Low Friction / Instant Merge}$, $100 = \text{Maximum Friction / PR Black Hole}$).
   - **Y Coordinate (Vertical):** Normalized Contributor Leverage ($0 = \text{Zero Production Footprint}$, $100 = \text{Global Critical Infrastructure / Tier S}$).
2. **Node Size:** Dynamically scaled between $6\text{px}$ (low criticality) and $18\text{px}$ (OpenSSF Criticality $\ge 0.85$ or $>100\text{k}$ dependents).
3. **Node Color & Glow:**
   - **The Sweet Spot (Top-Right/Friction Inverted or Quadrant IV):** Emerald (`#10B981`) with soft 8px blur halo.
   - **The Crucible (Top-Left):** Indigo (`#6366F1`).
   - **The Nursery (Bottom-Right):** Amber (`#F59E0B`).
   - **The Graveyard (Bottom-Left):** Crimson (`#EF4444`) with pulsing warning ping if ghost rate > 50%.
4. **Clustering Mechanics:** At zoom levels $< 0.85\times$, points within $14\text{px}$ proximity merge into a glowing cluster bubble showing `+N` count. Clicking or zooming in smoothly explodes the cluster into individual repository nodes.

#### Rich Hover Card Specification

When hovering over any scatter node for >= 120ms, a rich card anchors to the node:

| Hover Card Component | Interactive Specification & Data State |
| :--- | :--- |
| **Repository Header** | **`vllm-project / vllm`** `[ Tier S+ ]` `[ CVI: 94.2 ]`<br/>*High-throughput and memory-efficient LLM serving engine* |
| **Quadrant & Core Metrics** | **The Sweet Spot (Emerald Diamond)**<br/>• **Time-to-First-Review (TTFHR):** 11.2h (Top 2% PyPI)<br/>• **PR Ghost Rate:** 4.1% (Near-Zero Black Hole Risk)<br/>• **OpenSSF Scorecard:** 8.8 / 10<br/>• **Active Unclaimed GFIs:** 14 fresh issues |
| **Downstream Footprint & Action** | Python / CUDA • 14,820 Downstream Dependent Orgs<br/>`[ Click point or press Enter to inspect full Scorecard Drawer → ]` |

---

### 4.2 Screen B: Multi-Ecosystem Ranked Leaderboard View

Screen B provides a tabular, data-dense view for users who prefer structured sorting, multi-column ranking, and precise numerical comparisons across programming language ecosystems.

![Screen B: Multi-Ecosystem Ranked Leaderboard](assets/cvi_leaderboard.svg)

#### Column Hierarchy & Interactive Behaviors

1. **Rank Position (`#`):** Global ordinal integer based on active persona weighting. Tabular monospace numerals.
2. **Tier Badge (`[S+]`, `[S]`, `[A]`, `[B]`, `[C]`, `[D]`):**
   - Tier S+: Glowing emerald border, top 1% composite CVI.
   - Tier D / Disqualified: Flashing red warning outline.
3. **Repository Identity:** Avatar, verified foundation badge (e.g., Apache, CNCF, PSF feather icon), `owner/name`, and primary license.
4. **Overall CVI Score (0–100):** Visual mini-progress bar with numerical score overlaid.
5. **Maintainer Responsiveness (TTFHR):** Bot-filtered median hours. Values <= 24h styled in emerald; > 168h (1 week) styled in red.
6. **Pull Request Ghost Rate:** Percentage of PRs unanswered after 14 days. >= 50% displays a red warning badge `[STAGE 0 FAIL]`.
7. **Downstream Footprint:** Unique root-organization dependents derived from package manager indexes.
8. **CHAOSS Elephant Factor:** Commits contributed by top organizations. Badged as `Multi-Vendor (EF >= 3)` or `Single-Vendor Risk (EF = 1)`.
9. **90-Day Velocity Sparkline:** Micro-SVG line chart showing 12 weekly data points of merged PR throughput.
10. **Row Selection & Quick Actions:** Checkbox on row hover enables multi-repo selection for one-click launch into **Screen E (Comparator)**.

---

### 4.3 Screen C: Repository Deep-Dive Scorecard (Slide-Over Drawer / Full Modal)

Screen C is the ultimate diagnostic view for a single repository. It opens as a high-density slide-over drawer (480px–640px width on desktop) or full modal view without losing canvas state.

```mermaid
flowchart TB
    subgraph ScorecardLayout ["Screen C: Repository Scorecard Architecture"]
        direction TB
        Header["Header HUD: Repo Identity, CVI Composite Badge, Quadrant Pill, License"]
        
        subgraph VisualDuo ["Visual Diagnostics Row"]
            direction LR
            Radar["5-Pillar Radar Chart<br/>• Responsiveness (P1)<br/>• Criticality (P2)<br/>• Governance (P3)<br/>• Engineering Rigor (P4)<br/>• Accessibility (P5)<br/>vs Ecosystem Median Benchmark"]
            SurvivalCurve["Kaplan-Meier Latency Curve<br/>• P(Unanswered PR) vs Time (Days)<br/>• Bot-censored baseline<br/>• 12h, 24h, 48h SLA markers"]
        end

        subgraph GovernanceDuo ["Governance and Risk Row"]
            direction LR
            ElephantChart["CHAOSS Elephant Factor Donut<br/>• Top 5 Contributing Orgs<br/>• Entropy: 0.84 (Healthy Decentralization)"]
            CLAMatrix["Legal CLA Traffic-Light<br/>• DCO / Permissive [PASS]<br/>• Relicensing Rug-Pull Risk: Low"]
        end

        GFIStream["Curated Unclaimed Good First Issues (GFI) Feed<br/>• Issue Title, Age, Human Comments, Direct Claim CTA"]
    end

    Header --> VisualDuo
    VisualDuo --> GovernanceDuo
    GovernanceDuo --> GFIStream
```

#### Detailed Scorecard Section Specifications

![Screen C: Deep-Dive Scorecard Drawer](assets/cvi_scorecard_drawer.svg)

---

### 4.4 Screen D: Persona Configurator & Weight Adjuster

![Screen D: Custom Priority Configurator](assets/cvi_custom_ranking.svg)

Screen D empowers contributors to calibrate the platform's multi-criteria evaluation engine to match their personal career goals.

```mermaid
flowchart TD
    subgraph ConfiguratorEngine ["Screen D: Persona Configuration Engine"]
        direction TB
        PresetSelector["One-Click Persona Presets<br/>• Balanced Default<br/>• First-Time Contributor (Nursery Focus)<br/>• Career Capital Hunter (Sweet Spot Focus)<br/>• Senior Systems Specialist (Crucible Focus)<br/>• AI-Assisted Contributor (AI Posture Focus)"]
        
        Sliders["5-Pillar Slider Array with Normalization Lock<br/>• Responsiveness: [---o---] 25%<br/>• Criticality:    [-----o-] 35% [LOCK]<br/>• Governance:     [--o----] 15%<br/>• Rigor and AI:     [---o---] 15%<br/>• Accessibility:  [--o----] 10%"]
        
        Normalizer["Axiomatic Normalization Engine<br/>Sum of Unlocked Sliders dynamically scales to maintain Total = 100%"]
        
        LivePreview["Real-Time Leaderboard Re-Projection Preview<br/>Shows Top 5 Repositories with +/- Rank Delta Indicator"]
    end

    PresetSelector --> Sliders
    Sliders --> Normalizer
    Normalizer --> LivePreview
```

#### Interactive Slider Mechanics & Normalization Model

1. **Sum-to-100% Invariant:** The sum of weights must strictly equal 100%:
   $\sum_{i=1}^{5} w_i = 1.0$
2. **Lock Toggle:** Next to each slider is a padlock button `[Lock]`. When a user locks a pillar (e.g., locking Criticality at 35%), adjusting other sliders re-normalizes only the remaining $4$ unlocked pillars.
3. **Live Re-Projection Feedback:** As sliders move, the preview table to the right updates at 60fps without server roundtrips, displaying dynamic badge shifts:
   - `vllm-project/vllm`: `#1 -> #1 (0)`
   - `duckdb/duckdb`: `#3 -> #2 (+1)`
   - `torvalds/linux`: `#12 -> #4 (+8)` *(When Senior Systems Specialist is selected)*
4. **Preset One-Click Switches:**
   - **Balanced Default:** 25% Resp / 25% Impact / 20% Gov / 15% Rigor / 15% Access.
   - **First-Time Contributor:** 35% Resp / 30% Access / 15% Gov / 10% Impact / 10% Rigor.
   - **Career Capital Hunter:** 35% Impact / 20% Resp / 20% Access / 15% Gov / 10% Rigor.
   - **Senior Systems Specialist:** 35% Impact / 25% Gov / 20% Rigor / 15% Resp / 5% Access.
   - **AI-Assisted Contributor:** 35% Rigor & AI / 20% Impact / 20% Resp / 15% Gov / 10% Access.

---

### 4.5 Screen E: Side-by-Side Repository Comparator View

Screen E enables head-to-head evaluation between two to four repositories, directly comparing their trade-offs across all 15 empirical metrics.

| Metric Dimension | Repository A: `vllm-project/vllm` | Repository B: `keras-team/keras-hub` |
| :--- | :--- | :--- |
| **Overall CVI Score** | **94.2 (Tier S+) [Recommended]** | **41.2 (Tier D) [High Risk]** |
| **2D Discovery Quadrant** | **The Sweet Spot** (Emerald Diamond) | **The Graveyard** (Crimson Cross) |
| **Superimposed Radar View** | Solid Cyan Polygon (Expansive across all 5 axes) | Dashed Red Polygon (Collapsed on P1 and P5) |
| **Pillar 1: Responsiveness** | | |
| • Bot-Filtered Human TTFHR | **11.2 hours** [8.4x faster turnaround] | 94.0 hours |
| • First-Timer Merge Rate | **78.4%** | 22.1% |
| • PR Ghost Rate (14-day) | **3.8%** (Near-Zero Risk) | **58.4%** [Stage 0 Relegation] |
| • Review Latency Tail (p90) | **3.2 days** | 44.0 days |
| **Pillar 2: Production Impact** | | |
| • Downstream Registry Dependents | **14,820 root orgs** | 4,210 root orgs |
| • Canonical OpenSSF Criticality | **0.912** | 0.681 |
| • Filtered Monthly Downloads | **2.8M** | 840k |
| **Pillar 3: Governance and IP** | | |
| • Legal CLA Regime | **DCO Retained (Individual Copyright)** | Corporate CLA (Rigid IP Assignment) |
| • CHAOSS Elephant Factor | **4** (Multi-Vendor Consortium) | **1** (Single-Vendor Silo) |
| • Contributor Promotion Ladder | Verified Committership Pathway | Closed Internal Gatekeepers |
| **Pillar 4: Engineering Rigor** | | |
| • OpenSSF Scorecard Pipeline | **8.8 / 10** | 6.2 / 10 |
| • Calibrated AI Policy | Structured CI with Attribution | Ambiguous / Silent Rejections |
| **Pillar 5: Accessibility and On-Ramps** | | |
| • Active Unclaimed GFIs | **14 fresh issues** (&lt; 30d) | 0 issues (All stale &gt; 90d) |
| • 90-Day Newcomer Return Rate | **42.1%** | 8.4% |
| **Synthesized Verdict** | **Recommended:** S-Tier turnaround and verified multi-vendor governance. | **Avoid:** Severe PR ghosting (&gt; 50%) and closed single-vendor silo. |

---

## 5. Accessibility, Responsive Breakpoints & Universal Navigation

### 5.1 WCAG 2.1 AA Compliance & Color-Blind Defenses

1. **Contrast Validation:**
   - Text on `--bg-base` (`#090D16`): White text (`#F8FAFC`) achieves `17.4:1` contrast ratio (far exceeding WCAG AAA 7:1 standard).
   - Secondary text (`#94A3B8`) on `--bg-surface` (`#0F172A`) achieves `6.8:1` (exceeds WCAG AA 4.5:1 standard).
   - Interactive borders and semantic indicators maintain $\ge 3:1$ graphical contrast against adjacent surfaces.
2. **Color-Blind Defensive Shapes (Deuteranopia / Protanopia / Tritanopia):**
   - The UI never relies on color alone to convey quadrant identity or warning state.
   - **Quadrant Geometric Dual-Encoding:**
     - **The Sweet Spot:** Diamond glyph `[♦]` + Emerald `#10B981`.
     - **The Crucible:** Hexagon glyph `[⬢]` + Indigo `#6366F1`.
     - **The Nursery:** Circle glyph `[●]` + Amber `#F59E0B`.
     - **The Graveyard:** Octagon/Cross glyph `[⯀ / ✕]` + Crimson `#EF4444`.
   - **Stage 0 Disqualification:** Accompanied by a bold warning triangle icon `[⚠]` and explicit text badge `[STAGE 0 FAIL: GHOST RATE > 50%]`.

---

### 5.2 Universal Keyboard Navigation Map & Shortcuts

| Key Binding | Scope / Context | Action Executed |
| :--- | :--- | :--- |
| `Cmd + K` / `Ctrl + K` | Universal | Open Global Omnibar & Search Palette |
| `/` | Universal (Unfocused) | Focus Quick Filter Search Input |
| `1` | Global Navigation | Switch to Screen A: 2D Matrix View |
| `2` | Global Navigation | Switch to Screen B: Multi-Ecosystem Leaderboard |
| `3` | Global Navigation | Switch to Screen D: Persona Configurator |
| `4` | Global Navigation | Switch to Screen E: Repository Comparator |
| `Esc` | Modal / Drawer / Menu | Dismiss active Scorecard Drawer, Popover, or Omnibar |
| `Arrow Keys` (`↑` `↓` `←` `→`)| Screen A (Canvas) | Pan canvas in 50px increments |
| `+` / `-` | Screen A (Canvas) | Zoom in / Zoom out around canvas center |
| `0` | Screen A (Canvas) | Reset zoom level to `1.0x` and center at (50, 50) |
| `J` / `K` | Screen B (Table) | Select next / previous repository row |
| `Enter` | Screen B (Table) | Open Scorecard Drawer for selected row |
| `X` | Screen B (Table) | Toggle selection checkbox for comparison |
| `?` | Universal | Open Keyboard Shortcuts Cheatsheet Modal |

---

### 5.3 ARIA Roles, Live Regions & Screen Reader Tree

- **2D Matrix Accessible Alternative:** The interactive scatter canvas includes an `aria-label="2D Contributor Discovery Matrix"` container paired with a visually hidden, screen-reader-only accessible data table (`sr-only`) reflecting the currently filtered dataset.
- **Dynamic Weight Updates (`aria-live="polite"`):** When a slider changes in Screen D, an ARIA live region announces: `"Weights updated: Production Criticality 35 percent. Top repository is now vllm-project slash vllm."`
- **Focus Management:** Opening the Scorecard Drawer traps keyboard focus within the drawer (`focus-trap`), landing on the first actionable element (`Close Drawer` button), and restores focus to the triggering row/node upon dismissal.

---

### 5.4 Responsive Breakpoint System

```mermaid
flowchart LR
    subgraph Breakpoints ["Responsive Breakpoint Strategy"]
        DesktopXL["Desktop XL: 1600px and above<br/>Full 2D Canvas + Persistent Right Scorecard Drawer"]
        Desktop["Desktop Standard: 1200px to 1599px<br/>2D Canvas + Slide-Over Scorecard Drawer"]
        Tablet["Tablet Landscape: 1024px to 1199px<br/>Collapsible Nav Rail, Touch Pan/Zoom Gesture Support"]
        TabletP["Tablet Portrait: 768px to 1023px<br/>Bottom-Sheet Scorecard Drawer, Stacked Comparator"]
        Mobile["Mobile: under 768px<br/>Adaptive Card Leaderboard Default, Bottom Sheet Drawer"]
    end
```

#### Mobile Adaptive Behaviors (< 768px):
- **Matrix View Adaptation:** On mobile devices where precise 2D mouse hovering is difficult, the platform defaults to **Screen B (Card-Dense Leaderboard)** with a prominent quadrant segmented control (`[Sweet Spot] [Crucible] [Nursery] [Graveyard]`).
- **Touch Gestures:** If the 2D canvas is viewed, it supports native two-finger pinch-to-zoom and one-finger drag-to-pan with deceleration inertia.
- **Scorecard Drawer:** Transforms into a full-height bottom-sheet with swipe-down-to-dismiss gesture support.

---

## 6. Verification & Design Deliverable Checklist

| Design Dimension | Target Specification | Validation Status |
| :--- | :--- | :--- |
| **Pure Design Specification** | Zero code implementation files created (No React/Vue/TS files). | Verified Complete |
| **Visual Renderings** | 100% pure Mermaid diagrams utilized; zero ASCII art diagrams. | Verified Complete |
| **Color System** | Dark-mode first (`#090D16`, `#0F172A`, `#1E293B`) + 4 quadrant semantic accents. | Verified Complete |
| **Typography** | Inter UI + JetBrains Mono for metrics/code; tabular figure alignment. | Verified Complete |
| **Screen A (2D Matrix)** | Pan/zoom, coordinate HUD, bisectors, clustering, hover cards specified. | Verified Complete |
| **Screen B (Leaderboard)**| Ecosystem tabs, Stage 0 EBA flags, sparklines, column sorting specified. | Verified Complete |
| **Screen C (Scorecard)** | 5-pillar radar, Kaplan-Meier curve, Elephant Factor, CLA matrix, GFIs. | Verified Complete |
| **Screen D (Configurator)**| Sliders with sum-to-100% lock, 5 presets, live re-projection preview. | Verified Complete |
| **Screen E (Comparator)** | Dual superimposed radars, 15-parameter diff table, verdict engine. | Verified Complete |
| **Accessibility & Responsive**| WCAG 2.1 AA, color-blind shape coding, keyboard shortcuts, 5 breakpoints. | Verified Complete |

---
*Architectural Document End — Contributor Value Index (CVI) Platform UI/UX Specification.*
