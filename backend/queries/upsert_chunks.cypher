// upsert_chunks.cypher — search chunks with their embeddings, linked to their artifact (S3)
UNWIND $rows AS r
MATCH (a:Artifact {id: r.artifact_id})
MERGE (c:Chunk {id: r.id})
SET c.artifact_id = r.artifact_id, c.idx = r.idx, c.field = r.field,
    c.start_char = r.start_char, c.end_char = r.end_char, c.text = r.text,
    c.kind = r.kind, c.product_id = r.product_id, c.ts_ms = r.ts_ms,
    c.embedding = vecf32(r.embedding)
MERGE (a)-[:HAS_CHUNK]->(c)
