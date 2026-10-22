// upsert_tombstones.cypher — a content-free record of each forgotten item (id, kind, when, why), so a sync, sweep or backfill doesn't bring it back (F2, D125)
UNWIND $rows AS r
MERGE (t:Tombstone {id: r.id})
SET t.kind = r.kind, t.deleted_ms = r.now_ms, t.reason = r.reason
