"""Which stored records an older collector cut short, so that one fetch of exactly those clears the cut.

Before the caps were raised the commit list stopped at 1,000 rows, the author history at 500 pull requests and the
pull-request window at 500, and an envelope that reached its cap was marked ``truncated``; the ranking refuses a
truncated record. The current collectors read the whole window (the commit list to 5,000, the author history to
250,000 and the pull-request window to 2,000 with a reply sample above that), so refetching a record cut at an older,
smaller cap yields a complete one. A record cut at today's cap is genuinely too busy: fetching it again changes
nothing, so it is not listed here.
"""
from __future__ import annotations

import json
from pathlib import Path

from porchlamp.collect.commits import COMMIT_CAP
from porchlamp.collect.github_graphql import AUTHOR_WINDOW_CAP, PR_CAP

#: Collector name -> the cap its current collector records on an envelope that ran into it.
CUT_COLLECTORS: dict[str, int] = {
    "commits_365d": COMMIT_CAP,
    "author_history": AUTHOR_WINDOW_CAP,
    "pr_window": PR_CAP,
}


def legacy_cut_collectors(raw_dir: Path, owner: str, name: str) -> frozenset[str]:
    """The collectors of ``owner/name`` whose stored envelope is ``truncated`` at a cap below today's.

    A cut envelope that records no cap predates the field and counts as legacy. A missing, unreadable or non-object
    envelope is not a cut record (the ordinary layer pass collects what is missing).
    """
    cut: set[str] = set()
    for collector, current_cap in CUT_COLLECTORS.items():
        try:
            doc = json.loads((raw_dir / collector / f"{owner}__{name}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        data = doc.get("data") if isinstance(doc, dict) else None
        if not isinstance(data, dict) or data.get("truncated") is not True:
            continue
        cap = data.get("cap")
        if not isinstance(cap, int) or isinstance(cap, bool) or cap < current_cap:
            cut.add(collector)
    return frozenset(cut)
