// thread_context.cypher — a thread reply's conversation: the earlier replies of its thread, newest first; the root comes from message_context (F1, D126)
MATCH (c:Channel {id: $channel_id})<-[:IN_CHANNEL]-(m:Artifact)
WHERE m.kind = 'message' AND m.thread_id = $thread_id AND m.ts_ms < $before_ms AND m.ts_ms >= $since_ms
WITH m
OPTIONAL MATCH (p:Person)-[:AUTHORED]->(m)
RETURN m.id AS id, m.text AS text, m.ts_ms AS ts_ms, m.ts_iso AS ts_iso, m.system AS system, p.id AS author
ORDER BY m.ts_ms DESC
LIMIT $limit
