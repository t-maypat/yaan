// search_vector_scoped.cypher — exact nearest chunks within one product (cosine distance, lower = closer, A3), with filters (D042)
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(a:Artifact)-[:HAS_CHUNK]->(node:Chunk)
WHERE ($kinds IS NULL OR node.kind IN $kinds)
  AND ($since_ms IS NULL OR node.ts_ms >= $since_ms)
  AND ($until_ms IS NULL OR node.ts_ms < $until_ms)
WITH a, node, vec.cosineDistance(node.embedding, vecf32($qvec)) AS score
RETURN node.id AS chunk_id, node.text AS text, node.field AS field, a.id AS artifact_id,
       a.kind AS kind, a.title AS title, a.url AS url, a.ts_iso AS ts_iso, a.product_id AS product_id,
       score
ORDER BY score ASC
LIMIT $k
