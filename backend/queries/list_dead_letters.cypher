// list_dead_letters.cypher — the items connectors couldn't ingest, newest failure first, for status, retry and Graph health (H1, D107)
MATCH (d:DeadLetter)
WHERE $id IS NULL OR d.id = $id
RETURN d.id AS id, d.source AS source, d.artifact_id AS artifact_id, d.updated_ms AS updated_ms,
       d.reason AS reason, d.error AS error, d.attempts AS attempts, d.first_ms AS first_ms,
       d.last_ms AS last_ms, d.payload AS payload, d.events AS events
ORDER BY d.last_ms DESC, d.id
LIMIT $limit
