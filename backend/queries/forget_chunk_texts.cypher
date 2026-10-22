// forget_chunk_texts.cypher — the search-chunk texts of the artifacts `forget` removes: the embedding cache is keyed by text, so their vectors leave it too (F2, D125)
MATCH (a:Artifact)-[:HAS_CHUNK]->(c:Chunk)
WHERE a.id IN $ids
RETURN c.text AS text
LIMIT $limit
