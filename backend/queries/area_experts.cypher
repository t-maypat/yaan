// area_experts.cypher — the people who KNOW the given areas, best score first: degree-of-authorship, author flag, deliveries, discussion, recency score, PageRank rank (10.7)
UNWIND $ids AS id
MATCH (a:Area {id: id})<-[k:KNOWS]-(p:Person)
RETURN a.id AS area_id, p.id AS id, p.name AS name, p.role AS role, p.pagerank_rank AS pagerank_rank,
       k.author AS author, k.first AS first, k.doa_norm AS doa_norm, k.deliveries AS deliveries,
       k.discussion AS discussion, k.score AS score, k.last_ms AS last_ms
ORDER BY score DESC
LIMIT $limit
