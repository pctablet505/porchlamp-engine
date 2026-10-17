"""The API's own answers to many requests, called in-process on several processes.

``scripts/verify_static_export.py`` proves the static site answers each request of its grid as the API does;
the API's answers are the expected values (about 25,000 on the 11,000-repository snapshot). Each answer depends
on the database alone, so they can be computed on any number of processes; :func:`answers` returns them in the
order asked, the same whatever the worker count.
"""
from __future__ import annotations

import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Optional

#: ``(method, path, query params or None, JSON body or None)``.
Request = tuple[str, str, Optional[dict], Any]

_CHUNK = 256
_app: Any = None


def answer_of(response: Any) -> dict:
    """``{"ok": True, "data"}`` for a 200, else ``{"ok": False, "status", "detail"}``: what ``api.js`` returns."""
    if response.status_code == 200:
        return {"ok": True, "data": response.json()}
    return {"ok": False, "status": response.status_code, "detail": response.json()["error"]["detail"]}


def _start(db: str, evidence_files: Any) -> None:
    global _app
    from porchlamp.serve.app import create_app

    _app = create_app(db, web_dir=str(Path(db).parent / "__no_web__"), evidence_files=evidence_files)


def _answer_all(requests: list[Request]) -> list[dict]:
    from fastapi.testclient import TestClient

    with TestClient(_app) as client:  # one event loop for the whole list, not one per request
        return [answer_of(client.request(method, path, params=params, json=body))
                for method, path, params, body in requests]


def answers(db: Path, requests: list[Request], *, evidence_files: Any = None,
            workers: Optional[int] = None) -> list[dict]:
    """The API's answer to each of ``requests`` over the database ``db``, in order, on ``workers`` processes
    (``None``: one per CPU)."""
    count = workers if workers is not None else (os.cpu_count() or 1)
    chunks = [requests[i:i + _CHUNK] for i in range(0, len(requests), _CHUNK)]
    if count <= 1 or len(chunks) <= 1:
        _start(str(db), evidence_files)
        return [answer for chunk in chunks for answer in _answer_all(chunk)]
    # forkserver, not fork: forking a parent that has threads can deadlock the child.
    context = multiprocessing.get_context("forkserver")
    with ProcessPoolExecutor(max_workers=min(count, len(chunks)), mp_context=context,
                             initializer=_start, initargs=(str(db), evidence_files)) as pool:
        return [answer for part in pool.map(_answer_all, chunks) for answer in part]
