// forget_chunks.cypher — delete the search chunks (text and vector) of forgotten artifacts (F2, D125)
MATCH (a:Artifact)-[:HAS_CHUNK]->(c:Chunk)
WHERE a.id IN $ids
DETACH DELETE c
