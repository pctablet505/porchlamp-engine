"""CVI v2 integrity checks (wave W3): pipeline-integrity tools from
``docs/v2/03-ANTI-MANIPULATION.md`` section 5.

W3-3 ships the live re-verification canary (``cvi.verify.live_sample``),
which re-fetches a sample of rated rows from the live source and holds from
promotion any row whose recorded values no longer agree with it.
"""
