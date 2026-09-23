-- 0002_indexes.sql -- Read-path indexes for persona and reproject filtering.
--
-- EXPLAIN QUERY PLAN on synthetic snapshots shows list_ranked and reproject
-- predicates resolve through existing indexes, while the base filter
-- (`snapshot_id = ? AND eligible = 1`) benefits from indexing: idx_rank and
-- idx_eco key on `rated`/`ecosystem` rather than `eligible`.
--
-- The index is maintained to ensure an index seek rather than a table scan
-- as snapshots accumulate.
--
-- Note: Persona projection predicates (json_extract over persona_composites)
-- and custom-weight per-pillar CASE expressions cannot be indexed with fixed
-- expressions, as parameters vary per query.

CREATE INDEX IF NOT EXISTS idx_eligible ON repositories(snapshot_id, eligible);
