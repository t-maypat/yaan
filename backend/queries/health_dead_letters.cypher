// health_dead_letters.cypher — items connectors couldn't ingest, per source and reason: each waits in `yaan connectors dead` (H1, D107)
MATCH (d:DeadLetter)
WITH coalesce(d.source, '?') AS source, coalesce(d.reason, '?') AS reason, d
WITH source, reason, count(d) AS n, max(d.last_ms) AS last_ms
RETURN source, reason, n, last_ms
ORDER BY n DESC
LIMIT 100
