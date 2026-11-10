"""Pure planning: batches, the watch and priority lists, and the day's URL Inspection plan.

Search Console answers at most 2,000 URL Inspection calls a day for one property. A run inspects
the watch list (every non-project page in the sitemaps, plus the first, middle and last project of
each rank band of 1,000) and the day's slice of the project pages: the projects, in sitemap order
(rank order), are cut into slices of ``slice_size``, and the day whose ordinal is ``d`` takes slice
``d % slice_count``, so ``slice_count`` consecutive days inspect every project page once.

A ledger of the calls already made under the run's date keeps a second run on that date inside the
quota, and URLs already inspected under the date are not planned again (a run stopped by a 429 or
by Ctrl-C resumes where it stopped). The ledger counts by the run's own date; Google's quota day is
Pacific time, so a run near midnight can still meet a 429, which stops it.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

INDEXNOW_BATCH = 10_000  # URLs per IndexNow POST, the protocol's limit
BING_BATCH = 500  # URLs per Bing SubmitUrlBatch call
INSPECTION_DAILY_LIMIT = 2_000  # URL Inspection calls per day per property
INSPECTION_SLICE = 700  # project pages per day: 7 days cover 4,322, with the watch list 728 calls a day
RANK_BAND = 1_000
PRIORITY_PROJECTS = 50
_PROJECT_PATH = re.compile(r"/projects/[^/]+/[^/]+/")


def batched(items: Sequence[str], size: int) -> list[list[str]]:
    """``items`` in order, cut into lists of at most ``size``."""
    if size < 1:
        raise ValueError(f"a batch holds at least one item, not {size}")
    return [list(items[start:start + size]) for start in range(0, len(items), size)]


def is_project(url: str) -> bool:
    """True for a ranked project's page, ``/projects/<owner>/<name>/``."""
    return _PROJECT_PATH.fullmatch(urlsplit(url).path) is not None


def watch_list(urls: Sequence[str]) -> list[str]:
    """The pages inspected every day: each non-project page, then three projects per rank band."""
    projects = [url for url in urls if is_project(url)]
    picks: list[str] = []
    for start in range(0, len(projects), RANK_BAND):
        band = projects[start:start + RANK_BAND]
        picks.extend(band[index] for index in sorted({0, len(band) // 2, len(band) - 1}))
    return [*(url for url in urls if not is_project(url)), *picks]


def priority_list(urls: Sequence[str], top: int = PRIORITY_PROJECTS) -> list[str]:
    """What Bing gets by direct submission: each non-project page, then the first ``top`` projects."""
    projects = [url for url in urls if is_project(url)]
    return [*(url for url in urls if not is_project(url)), *projects[:top]]


@dataclass(frozen=True)
class InspectionPlan:
    """One run's URL Inspection calls, and how they were chosen."""

    day: date
    slice_index: int  # 0-based
    slice_count: int
    slice_first: int  # 1-based sitemap position (among projects) of the slice's first project
    slice_last: int
    watch: int  # URLs on the watch list
    wanted: int  # watch list and slice, each once, less the URLs already inspected under ``day``
    used: int  # calls already made under ``day``
    limit: int
    urls: tuple[str, ...]  # this run's calls: the watch list first

    @property
    def cut(self) -> int:
        """Wanted URLs left out because the day's quota has no room for them."""
        return self.wanted - len(self.urls)


def inspection_plan(
    urls: Sequence[str],
    day: date,
    *,
    used: int = 0,
    done: Collection[str] = (),
    slice_size: int = INSPECTION_SLICE,
    limit: int = INSPECTION_DAILY_LIMIT,
) -> InspectionPlan:
    """The URL Inspection calls for ``day``: the watch list and the day's slice, within the quota."""
    if slice_size < 1:
        raise ValueError(f"a slice holds at least one project, not {slice_size}")
    projects = [url for url in urls if is_project(url)]
    count = max(1, -(-len(projects) // slice_size))
    index = day.toordinal() % count
    start = index * slice_size
    rotation = projects[start:start + slice_size]
    watch = watch_list(urls)
    finished = set(done)
    wanted = [url for url in dict.fromkeys([*watch, *rotation]) if url not in finished]
    room = max(0, limit - used)
    return InspectionPlan(
        day=day,
        slice_index=index,
        slice_count=count,
        slice_first=start + 1,
        slice_last=start + len(rotation),
        watch=len(watch),
        wanted=len(wanted),
        used=used,
        limit=limit,
        urls=tuple(wanted[:room]),
    )
