"""Build the criteria-based candidate universe -> data/raw/candidates.jsonl.

Criteria: top-N per ecosystem by GitHub stars (search API) plus registry
top-lists where free. Output line: {"owner","name","ecosystem",
"selected_by":[...],"stars_at_selection":int,"added_at":iso}.
Dedup by (owner,name) lowercased, merging selected_by; cap --max-total.
Checkpoint: data/raw/universe_checkpoint.json (rerun skips completed pages).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DATA = Path(__file__).resolve().parent.parent / "data" / "raw"
CHECKPOINT = DATA / "universe_checkpoint.json"
CANDIDATES = DATA / "candidates.jsonl"

# ecosystem -> GitHub search language queries (page count at 100/page)
SEARCH_CRITERIA: dict[str, list[tuple[str, int]]] = {
    "pypi": [("language:Python", 10)],
    "npm": [("language:JavaScript", 10), ("language:TypeScript", 5)],
    "crates": [("language:Rust", 10)],
    "go": [("language:Go", 10)],
    "maven": [("language:Java", 10)],
    "systems": [("language:C", 5), ("language:C++", 5)],
}


def gh_search(query: str, page: int, per_page: int = 100) -> list[dict[str, Any]]:
    """One GitHub search page via `gh api` (authed; 30 req/min limit)."""
    cmd = ["gh", "api", "search/repositories",
           "-X", "GET", "-f", f"q={query}", "-F", f"page={page}",
           "-F", f"per_page={per_page}", "--jq",
           '.items[] | {owner: .owner.login, name: .name, stars: .stargazers_count}']
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"gh search failed rc={out.returncode}: {out.stderr[:200]}")
    return [json.loads(line) for line in out.stdout.splitlines() if line.strip()]


def crates_top(limit: int) -> list[dict[str, Any]]:
    """Top crates.io packages by downloads (User-Agent required)."""
    req = urllib.request.Request(
        "https://crates.io/api/v1/crates?sort=downloads&per_page=100",
        headers={"User-Agent": "cvi-research/0.1 (ranking study)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    hits = []
    for c in data.get("crates", [])[:limit]:
        repo = c.get("repository") or ""
        if "github.com/" in repo:
            parts = repo.split("github.com/")[1].strip("/").split("/")
            if len(parts) >= 2:
                # crates.io repository URLs sometimes carry a trailing ".git"
                # (e.g. github.com/fizyk20/generic-array.git); the GitHub API
                # 404s on that name, so strip it before it becomes a candidate.
                hits.append({"owner": parts[0], "name": parts[1].removesuffix(".git"),
                             "stars": int(c.get("downloads") or 0)})
    return hits


def _load_ckpt() -> dict[str, Any]:
    if CHECKPOINT.is_file():
        return json.loads(CHECKPOINT.read_text())
    return {"done": {}}


def _save_ckpt(ck: dict[str, Any]) -> None:
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT.write_text(json.dumps(ck, indent=1))


def build(max_per_criterion: int = 1000, max_total: int = 10000,
          only: Optional[str] = None, dry_run: bool = False,
          sleep_s: float = 2.0) -> int:
    """Run the universe build; return candidate count written."""
    ck = _load_ckpt()
    found: dict[tuple[str, str], dict[str, Any]] = {}
    if CANDIDATES.is_file():
        for line in CANDIDATES.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                found[(r["owner"].lower(), r["name"].lower())] = r

    def add(owner: str, name: str, eco: str, crit: str, stars: int) -> None:
        key = (owner.lower(), name.lower())
        if key in found:
            if crit not in found[key]["selected_by"]:
                found[key]["selected_by"].append(crit)
            return
        found[key] = {"owner": owner, "name": name, "ecosystem": eco,
                      "selected_by": [crit], "stars_at_selection": stars,
                      "added_at": datetime.now(timezone.utc).isoformat()}

    pages = max(1, min(10, max_per_criterion // 100))
    for eco, crits in SEARCH_CRITERIA.items():
        if only and eco != only:
            continue
        for query, max_pages in crits:
            for page in range(1, min(max_pages, pages) + 1):
                cid = f"search:{eco}:{query}:{page}"
                if cid in ck["done"]:
                    continue
                if dry_run:
                    print(f"[dry] {cid}")
                    continue
                time.sleep(sleep_s)
                try:
                    items = gh_search(query, page)
                except RuntimeError as e:
                    print(f"WARN {cid}: {e}")
                    continue
                for it in items:
                    add(it["owner"], it["name"], eco, f"github-stars:{query}",
                        int(it["stars"]))
                ck["done"][cid] = len(items)
                _save_ckpt(ck)
                print(f"{cid}: {len(items)} items")
        if only == "crates" or (only is None and eco == "crates"):
            try:
                for it in crates_top(max_per_criterion):
                    add(it["owner"], it["name"], "crates", "crates-downloads", it["stars"])
            except Exception as e:  # review-ok: gate-network — registry list optional
                print(f"WARN crates_top: {e}")

    rows = sorted(found.values(), key=lambda r: -r["stars_at_selection"])[:max_total]
    if not dry_run:
        CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
        CANDIDATES.write_text("".join(json.dumps(r) + "\n" for r in rows))
        print(f"wrote {len(rows)} candidates -> {CANDIDATES}")
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-per-criterion", type=int, default=1000)
    ap.add_argument("--max-total", type=int, default=10000)
    ap.add_argument("--only", choices=list(SEARCH_CRITERIA))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    build(a.max_per_criterion, a.max_total, a.only, a.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
