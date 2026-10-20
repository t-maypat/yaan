// upsert_dead_letters.cypher — items a connector couldn't ingest, with the reason and the item itself so `yaan connectors retry` can replay them (H1, D107)
UNWIND $rows AS r
MERGE (d:DeadLetter {id: r.id})
SET d.source = r.source,
    d.artifact_id = r.artifact_id,
    d.updated_ms = r.updated_ms,
    d.reason = r.reason,
    d.error = r.error,
    d.attempts = r.attempts,
    d.first_ms = coalesce(d.first_ms, r.now_ms),
    d.last_ms = r.now_ms,
    d.payload = r.payload,
    d.events = r.events
RETURN count(d) AS n
