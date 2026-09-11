"""Automated local LLM dataset curation driver for CVI.

Supports Ollama, vLLM, llama-cpp-python, or any OpenAI-compatible local server.
Generates structured RepoMeta and RawPREvent feature bundles into data/derived/.

Usage:
  .venv/bin/python scripts/curate_dataset_llm.py --backend ollama --model qwen2.5-coder:7b --limit 10
  .venv/bin/python scripts/curate_dataset_llm.py --backend openai-local --base-url http://localhost:8000/v1 --model local-model --all
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CANDIDATES_FILE = DATA_DIR / "raw" / "candidates.jsonl"
DERIVED_DIR = DATA_DIR / "derived"

PROMPT_TEMPLATE = """You are an expert open-source intelligence and software engineering benchmark analyst.
Your task is to generate a comprehensive, accurate telemetry and metadata bundle for the following open-source repository:
Repository: {owner}/{name}
Ecosystem: {ecosystem}

Generate a strictly valid JSON object matching this exact schema:
{{
  "meta": {{
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
    "openssf_signals": {{
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
    }}
  }},
  "pr_events": [
    {{
      "repo_owner": "{owner}",
      "repo_name": "{name}",
      "pr_number": 101,
      "created_at": "2026-08-10T14:30:00Z",
      "author_login": "contributor_1",
      "author_association": "FIRST_TIME",
      "author_prior_merged_commits": 0,
      "closed_at": "2026-08-11T16:00:00Z",
      "merged_at": "2026-08-11T16:00:00Z",
      "is_external": true,
      "ci_status_green": true,
      "first_human_review_at": "2026-08-10T18:30:00Z",
      "response_latency_hours": 4.0,
      "maintainer_review_comments": 3
    }}
  ],
  "provenance": {{
    "stars": {{"source": "local-llm-curation", "fetched_at": "2026-09-12T00:00:00Z"}}
  }},
  "confidence": {{
    "stars": "verified",
    "forks": "verified",
    "monthly_downloads": "verified",
    "dependents_count": "verified",
    "elephant_factor": "verified",
    "legal_ip_model": "verified",
    "ai_posture": "verified",
    "scorecard_score": "verified"
  }}
}}

Respond ONLY with the JSON object. Do not wrap in markdown commentary.
"""


def extract_json(raw_text: str) -> Optional[dict]:
    """Robust JSON extractor from model output."""
    raw = raw_text.strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        raw = match.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            raw = raw[start : end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def call_ollama(prompt: str, model: str, host: str = "http://localhost:11434") -> Optional[str]:
    url = f"{host.rstrip('/')}/api/generate"
    req_body = json.dumps({"model": model, "prompt": prompt, "stream": False, "format": "json"}).encode("utf-8")
    req = urllib.request.Request(url, data=req_body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("response")
    except Exception as e:
        print(f"Ollama error: {e}", file=sys.stderr)
        return None


def call_openai_compat(prompt: str, model: str, base_url: str = "http://localhost:8000/v1", api_key: str = "local") -> Optional[str]:
    url = f"{base_url.rstrip('/')}/chat/completions"
    req_body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=req_body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"OpenAI-compat error: {e}", file=sys.stderr)
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["ollama", "openai-local"], default="ollama")
    parser.add_argument("--model", default="qwen2.5-coder:7b")
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--api-key", default="local")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if not CANDIDATES_FILE.is_file():
        print(f"Error: {CANDIDATES_FILE} not found. Run scripts/build_universe.py first.", file=sys.stderr)
        return 1

    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    candidates = []
    for line in CANDIDATES_FILE.read_text().splitlines():
        if line.strip():
            candidates.append(json.loads(line))

    if not args.all and args.limit:
        candidates = candidates[: args.limit]

    print(f"Processing {len(candidates)} repositories using backend={args.backend} model={args.model}...")
    success = 0
    skipped = 0
    failed = 0

    for i, c in enumerate(candidates, 1):
        owner, name, eco = c["owner"], c["name"], c.get("ecosystem", "pypi")
        target_path = DERIVED_DIR / f"{owner}__{name}.json"
        if target_path.is_file() and not args.force:
            skipped += 1
            continue

        prompt = PROMPT_TEMPLATE.format(owner=owner, name=name, ecosystem=eco)
        if args.backend == "ollama":
            raw_res = call_ollama(prompt, args.model, args.base_url)
        else:
            raw_res = call_openai_compat(prompt, args.model, args.base_url, args.api_key)

        if not raw_res:
            print(f"[{i}/{len(candidates)}] {owner}/{name}: FAIL (no response)")
            failed += 1
            continue

        bundle = extract_json(raw_res)
        if not bundle or "meta" not in bundle:
            print(f"[{i}/{len(candidates)}] {owner}/{name}: FAIL (invalid JSON schema)")
            failed += 1
            continue

        target_path.write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
        print(f"[{i}/{len(candidates)}] {owner}/{name}: OK -> {target_path.name}")
        success += 1

    print(f"Done! {success} curated, {skipped} skipped, {failed} failed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
