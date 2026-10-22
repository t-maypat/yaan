// message_context.cypher — one message as conversation context (a thread's root, read with every reply however old) (F1, D126)
MATCH (m:Artifact {id: $id})
WITH m
OPTIONAL MATCH (p:Person)-[:AUTHORED]->(m)
RETURN m.id AS id, m.text AS text, m.ts_ms AS ts_ms, m.ts_iso AS ts_iso, m.system AS system, p.id AS author
LIMIT 1
