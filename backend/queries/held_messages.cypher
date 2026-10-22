// held_messages.cypher — the messages from one source channel the graph holds since a moment, with their text and thread: what the reconciliation sweep compares with the source (F1, F7, D126)
MATCH (c:Channel {id: $channel_id})<-[:IN_CHANNEL]-(m:Artifact)
WHERE m.kind = 'message' AND m.ts_ms >= $since_ms AND m.id STARTS WITH $prefix
RETURN m.id AS id, m.text AS text, m.thread_id AS thread_id
LIMIT $limit
