// forget_alerts.cypher — alerts a forgotten artifact raised lose its words, and an open one is withdrawn; their ids come back so their drafted actions follow (F2, D125)
MATCH (a:Alert)
WHERE a.artifact_id IN $ids
SET a.quote = null, a.reason = null, a.subject_title = '', a.summary = $summary,
    a.forgotten_ms = $now_ms,
    a.status = CASE WHEN a.status = 'open' THEN 'withdrawn' ELSE a.status END
RETURN a.id AS id
