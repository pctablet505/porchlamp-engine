"""Run scripts/gather.py with a smaller GraphQL page for the pr_window collector (layer L1 only).

Why: a 100-PR page of the PrWindow query (reviews + comments per PR) exceeds GitHub's server-side time budget on
very active repos and returns HTTP 504 or a truncated body, every time. collect_pr_window already takes
``page_size`` for this ("drop it for very active repositories", github_graphql.py). gather.py does not pass it,
so this wrapper binds it before gather.py's call-time import. The envelope is identical (same PRs, same order,
same PR_CAP); only ``cost_points`` differs. Probe 2026-10-08: Expensify/App 504s at 100, OK at 25 (102 s) and 10.

A page that still fails with a query-weight error (504/502/truncated body/timeout) is read again in pages of 10 by
the collector itself, which then goes on at PR_PAGE_SIZE (``github_graphql._PageSizer``). A repo that still fails
is not retried here: the next pass does that.

Usage (cwd = the worktree, PYTHONPATH set by gather_layer.sh): python gather_pagesize.py <gather.py args>
"""
import os
import runpy
import sys

from porchlamp.collect import github_graphql as g

PAGE = int(os.environ.get("PR_PAGE_SIZE", "25"))
_orig = g.collect_pr_window


def collect_pr_window(*args, **kwargs):
    kwargs.setdefault("page_size", PAGE)
    return _orig(*args, **kwargs)


g.collect_pr_window = collect_pr_window
print(f"gather_pagesize: collect_pr_window page_size={PAGE}; a failing page is read again in pages of 10",
      flush=True)

sys.argv = ["scripts/gather.py"] + sys.argv[1:]
runpy.run_path("scripts/gather.py", run_name="__main__")
