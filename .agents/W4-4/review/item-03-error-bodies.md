# Item 3 — error bodies and the UI's parsing (L3) — PASS shape / FAIL classification / client gap

Shape is consistent on 404/422/405/503 — `{"error": {endpoint, status, detail}}`
on all ten probed cases (report has the table).

Two problems:
1. **Classification.** `@app.exception_handler(ValueError)` also catches
   `pydantic_core.ValidationError` (a `ValueError` subclass), so a server-side
   fault is reported as a client 422 with raw pydantic text:
   - stored `quadrant='Nowhere'` → `422 "1 validation error for LeaderboardRow…"`
   - evidence row with an unknown `param_id` → `422`, **not** the 500 that
     `porchlamp/serve/app.py`'s bridge item 10 and the handoff both state as the
     lead's explicit ruling.
   → Blocker B-3.
2. **A genuine 500 escapes the contract**: on a v1-shaped DB
   `/v1/porchlamp/leaderboard` returns plain text `Internal Server Error`. → W-7.

`web/v2/api.js` (main) **does not parse the body**: on `!response.ok` it
returns `new ApiError(endpoint, response.status)` with no message, so every
`detail` this packet produces is discarded. → W-2.
