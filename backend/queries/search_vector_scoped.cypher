// search_vector_scoped.cypher — exact nearest chunks within one product (cosine distance, lower = closer, A3), with filters (D042); for a viewer, only what they may read, before the limit (D131)
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(a:Artifact)-[:HAS_CHUNK]->(node:Chunk)
WHERE ($kinds IS NULL OR node.kind IN $kinds)
  AND ($since_ms IS NULL OR node.ts_ms >= $since_ms)
  AND ($until_ms IS NULL OR node.ts_ms < $until_ms)
WITH a, node
OPTIONAL MATCH (a)-[:IN_CHANNEL]->(c:Channel)
WITH a, node, c
WHERE NOT $scoped
   OR (a.kind = 'meeting' AND $reach_meetings IS NOT NULL AND a.id IN $reach_meetings)
   OR (NOT (a.kind = 'meeting' AND $reach_meetings IS NOT NULL)
       AND ((c IS NOT NULL AND c.id IN $reach_channels)
            OR (c IS NULL AND ($reach_unchanneled
                OR any(s IN $reach_sources WHERE split(a.id, ':')[1] STARTS WITH s + '-')
                OR (a.repo IS NOT NULL AND a.repo IN $reach_repos)))))
WITH a, node, vec.cosineDistance(node.embedding, vecf32($qvec)) AS score
RETURN node.id AS chunk_id, node.text AS text, node.field AS field, a.id AS artifact_id,
       a.kind AS kind, a.title AS title, a.url AS url, a.ts_iso AS ts_iso, a.product_id AS product_id,
       score
ORDER BY score ASC
LIMIT $k
