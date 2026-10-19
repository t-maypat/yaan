// channel_context.cypher — the messages before a new one in its channel, newest first: the conversation the live extraction reads it in, as the batch extraction does (D102)
MATCH (c:Channel {id: $channel_id})<-[:IN_CHANNEL]-(m:Artifact)
WHERE m.kind = 'message' AND m.ts_ms < $before_ms AND m.ts_ms >= $since_ms
WITH m
OPTIONAL MATCH (p:Person)-[:AUTHORED]->(m)
RETURN m.id AS id, m.text AS text, m.ts_ms AS ts_ms, m.ts_iso AS ts_iso, m.system AS system, p.id AS author
ORDER BY m.ts_ms DESC
LIMIT $limit
