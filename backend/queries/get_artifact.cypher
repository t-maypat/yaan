// get_artifact.cypher — one artifact's full text, its channel, and its other quotable fields (PR reviews, doc feedback) from its chunks
MATCH (a:Artifact {id: $id})
OPTIONAL MATCH (a)-[:IN_CHANNEL]->(ch:Channel)
OPTIONAL MATCH (a)-[:HAS_CHUNK]->(c:Chunk)
WHERE c.field <> 'text' AND c.field <> 'title'
WITH a, ch, c
ORDER BY c.idx
WITH a, ch, collect({field: c.field, text: c.text}) AS fields
RETURN a.id AS id, a.kind AS kind, a.title AS title, a.url AS url, a.ts_iso AS ts_iso,
       a.text AS text, a.product_id AS product_id, ch.name AS channel, fields
LIMIT 1
