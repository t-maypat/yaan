// area_reviews.cypher — every review of an internal pull request: who, which PR, when (10.1 COLLAB, 10.2 discussion signal)
MATCH (p:Person)-[r:REVIEWED]->(pr:PR)
WHERE coalesce(pr.external, false) = false
RETURN p.id AS person_id, pr.id AS pr_id, r.ts_ms AS ts_ms
LIMIT $limit
