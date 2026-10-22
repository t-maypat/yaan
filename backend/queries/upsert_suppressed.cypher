// upsert_suppressed.cypher — a hash of an erased person's id, so a sync, sweep or backfill doesn't ingest their items again while a source still holds them (F2, D125)
MERGE (s:Suppressed {id: $id})
SET s.erased_ms = $now_ms
