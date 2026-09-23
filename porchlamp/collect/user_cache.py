"""Global user cache for author eligibility (08 D-COL-5, packet W6-5).

A login's ``createdAt`` never changes and its public-event count (the 1-y
contribution calendar total, D-IN-3's proxy) moves slowly, yet the
author-history collector used to look both up again for every repository an
author touched. This cache keeps one file per login at
``<data_root>/state/users/<login>.json`` so a login is looked up once across
the whole universe and again only after :data:`USER_CACHE_TTL_DAYS`.

MEASURED 2026-09-22 on the live API: one ``AuthorEligibilityBatch`` call of 15
logins takes 6.3-9.9 s (the contribution calendar is the slow field; a batch
of 30 timed out and returned nothing, 50 hit ``RESOURCE_LIMITS_EXCEEDED``).
Across the L1 PR windows, 134,036 (repo, external author) pairs name only
94,235 distinct logins, so the same sweep needs 30 % fewer lookups with the
cache -- and a later sweep needs lookups only for logins never seen before.

What the cache is not: evidence. ``derive`` never reads it. The
``author_history`` envelope still embeds every value it used, and each record
there carries ``eligibility_observed_at``, the time that value was actually
fetched, so a value reused from an earlier lookup is never presented as one
fetched with the envelope.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional

#: D-COL-5: the public-event count is reused for at most 90 days.
USER_CACHE_TTL_DAYS: int = 90

#: Every record the cache writes names the query that produced it.
USER_CACHE_SOURCE: str = "github-graphql:AuthorEligibilityBatch"

#: The logins the cache stores: a lower-cased GitHub login is letters,
#: digits and hyphens, plus the underscore of managed-user logins
#: (``name_shortcode``). Anything else -- dots, slashes, an empty string --
#: is never turned into a path; such a login is simply looked up uncached.
_CACHEABLE_LOGIN = re.compile(r"[a-z0-9][a-z0-9_-]{0,99}")


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


class UserCache:
    """One JSON file per login under ``root``; counts what it did in ``stats``.

    :meth:`get` answers only with a record observed inside the TTL and not
    after ``now`` -- a lookup made later than the run's own clock is never
    used, so a backdated collection cannot read the future. :meth:`put`
    stores only complete, measured records; an ``eligibility_unmeasured``
    outcome is never cached, so a failed lookup is retried next time rather
    than remembered.
    """

    def __init__(self, root: Path, ttl_days: int = USER_CACHE_TTL_DAYS) -> None:
        self.root = Path(root)
        self.ttl = timedelta(days=ttl_days)
        self.stats: dict[str, int] = {
            "hits": 0, "misses": 0, "expired": 0, "unreadable": 0,
            "uncacheable": 0, "writes": 0,
        }

    def _path(self, login: str) -> Optional[Path]:
        if not _CACHEABLE_LOGIN.fullmatch(login):
            return None
        return self.root / f"{login}.json"

    def get(self, login: str, now: datetime) -> Optional[dict[str, Any]]:
        """The cached eligibility of ``login`` as of ``now``, or ``None``.

        Returns ``{"account_created_at", "public_event_count",
        "observed_at"}``. ``None`` means "look it up": no file, an
        unreadable or malformed file, a record older than the TTL, one
        observed after ``now``, or a login the cache never stores.
        """
        path = self._path(login)
        if path is None:
            self.stats["uncacheable"] += 1
            return None
        try:
            doc = json.loads(path.read_text())
        except FileNotFoundError:
            self.stats["misses"] += 1
            return None
        except (OSError, ValueError):
            self.stats["unreadable"] += 1
            return None
        observed = _parse_iso(doc.get("observed_at")) if isinstance(doc, dict) else None
        account = doc.get("account_created_at") if isinstance(doc, dict) else None
        events = doc.get("public_event_count") if isinstance(doc, dict) else None
        if (observed is None or not isinstance(account, str)
                or isinstance(events, bool) or not isinstance(events, int)):
            self.stats["unreadable"] += 1
            return None
        if observed > now or now - observed > self.ttl:
            self.stats["expired"] += 1
            return None
        self.stats["hits"] += 1
        return {"account_created_at": account, "public_event_count": events,
                "observed_at": doc["observed_at"]}

    def put(self, login: str, record: Mapping[str, Any], observed_at: datetime) -> bool:
        """Store one measured record; returns whether it was written.

        Written atomically (temporary file, then ``os.replace``), so a
        reader never sees half a record.
        """
        path = self._path(login)
        if path is None or record.get("eligibility_unmeasured"):
            return False
        account = record.get("account_created_at")
        events = record.get("public_event_count")
        if not isinstance(account, str) or isinstance(events, bool) or not isinstance(events, int):
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({
            "login": login, "typename": "User", "account_created_at": account,
            "public_event_count": events, "observed_at": observed_at.isoformat(),
            "source": USER_CACHE_SOURCE,
        }, sort_keys=True))
        os.replace(tmp, path)
        self.stats["writes"] += 1
        return True

    def summary(self) -> str:
        """One line for the gather log."""
        s = self.stats
        return (f"user cache: {s['hits']} hits, {s['misses']} misses, "
                f"{s['expired']} expired, {s['unreadable']} unreadable, "
                f"{s['uncacheable']} uncacheable, {s['writes']} written")
