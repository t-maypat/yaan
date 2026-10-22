// search_fulltext.cypher — keyword hits on chunk text (higher score = better), with filters; for a viewer, only what they may read, before the limit (D131)
CALL db.idx.fulltext.queryNodes('Chunk', $q) YIELD node, score
WITH node, score
WHERE ($kinds IS NULL OR node.kind IN $kinds)
  AND ($since_ms IS NULL OR node.ts_ms >= $since_ms)
  AND ($until_ms IS NULL OR node.ts_ms < $until_ms)
MATCH (a:Artifact)-[:HAS_CHUNK]->(node)
WHERE $product_id IS NULL OR (a)-[:IN_PRODUCT]->(:Product {id: $product_id})
WITH node, score, a
OPTIONAL MATCH (a)-[:IN_CHANNEL]->(c:Channel)
WITH node, score, a, c
WHERE NOT $scoped
   OR (a.kind = 'meeting' AND $reach_meetings IS NOT NULL AND a.id IN $reach_meetings)
   OR (NOT (a.kind = 'meeting' AND $reach_meetings IS NOT NULL)
       AND ((c IS NOT NULL AND c.id IN $reach_channels)
            OR (c IS NULL AND ($reach_unchanneled
                OR any(s IN $reach_sources WHERE split(a.id, ':')[1] STARTS WITH s + '-')
                OR (a.repo IS NOT NULL AND a.repo IN $reach_repos)))))
RETURN node.id AS chunk_id, node.text AS text, node.field AS field, a.id AS artifact_id,
       a.kind AS kind, a.title AS title, a.url AS url, a.ts_iso AS ts_iso, a.product_id AS product_id,
       score
ORDER BY score DESC
LIMIT $k
