"""CVI v2 relational store (`04` section 5).

``schema.py`` is the DDL; ``migrations/`` applies it forward-only (v1 rows
preserved under ``legacy_repositories``, `04` section 10); ``writer.py``
inserts a snapshot's ``ScoreRecord``s, their long-format parameter evidence
and frozen populations; ``reader.py`` has the single-row/evidence/snapshot
getters plus the ordering helper the SQL persona-projection query builds on.
"""
from __future__ import annotations
