"""Contributor Value Index (CVI) ranking engine.

Empirical analytics engine that ranks open-source repositories by
contributor return-on-investment (ROI), modelling the trade-off between
Contributor Leverage (Y) and Contributor Friction (X).

The package is pure-Python (stdlib only) for the scoring core so the math
is fully testable without I/O. Ingestion (DuckDB / GH Archive / GraphQL) and
serving (FastAPI) layers are intentionally out of scope for the core.
"""

__version__ = "0.1.0"
