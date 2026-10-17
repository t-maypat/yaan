// area_prs.cypher — the internal pull requests with author, time, the PR each one reverts and the issue it fixes: the input for work-item areas (10.0, D059)
MATCH (pr:PR)
WHERE coalesce(pr.external, false) = false
OPTIONAL MATCH (au:Person)-[:AUTHORED]->(pr)
OPTIONAL MATCH (pr)-[:REVERTS]->(orig:PR)
OPTIONAL MATCH (pr)-[:FIXES]->(fi:Issue)
RETURN pr.id AS id, pr.product_id AS product_id, pr.title AS title, pr.number AS number,
       pr.ts_ms AS ts_ms, au.id AS author, orig.id AS reverts, fi.id AS fixes
LIMIT $limit
