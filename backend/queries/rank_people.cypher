// rank_people.cypher — engineers of a product ranked by one metric, counted in the graph: PRs authored / merged / reviewed / approved / sent back, bugs fixed (authored a PR that FIXES an issue), open bugs assigned; each row carries up to 5 artifact or issue ids as evidence (tool 7 rank, R2)
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(pr:PR)<-[:AUTHORED]-(p:Person)
WHERE $metric = 'prs_authored' AND coalesce(pr.external, false) = false
WITH p, count(DISTINCT pr) AS n, collect(DISTINCT pr.id) AS ids
RETURN p.id AS id, p.name AS name, p.role AS role, n, ids[0..5] AS evidence
ORDER BY n DESC, id
LIMIT $limit
UNION ALL
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(pr:PR)<-[:AUTHORED]-(p:Person)
WHERE $metric = 'prs_merged' AND coalesce(pr.external, false) = false AND pr.current_status = 'merged'
WITH p, count(DISTINCT pr) AS n, collect(DISTINCT pr.id) AS ids
RETURN p.id AS id, p.name AS name, p.role AS role, n, ids[0..5] AS evidence
ORDER BY n DESC, id
LIMIT $limit
UNION ALL
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(pr:PR)<-[r:REVIEWED]-(p:Person)
WHERE $metric = 'prs_reviewed' AND coalesce(pr.external, false) = false
WITH p, count(DISTINCT pr) AS n, collect(DISTINCT pr.id) AS ids
RETURN p.id AS id, p.name AS name, p.role AS role, n, ids[0..5] AS evidence
ORDER BY n DESC, id
LIMIT $limit
UNION ALL
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(pr:PR)<-[r:REVIEWED]-(p:Person)
WHERE $metric = 'prs_approved' AND coalesce(pr.external, false) = false AND r.state = 'APPROVED'
WITH p, count(DISTINCT pr) AS n, collect(DISTINCT pr.id) AS ids
RETURN p.id AS id, p.name AS name, p.role AS role, n, ids[0..5] AS evidence
ORDER BY n DESC, id
LIMIT $limit
UNION ALL
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(pr:PR)<-[r:REVIEWED]-(p:Person)
WHERE $metric = 'changes_requested' AND coalesce(pr.external, false) = false
  AND r.state = 'CHANGES_REQUESTED'
WITH p, count(DISTINCT pr) AS n, collect(DISTINCT pr.id) AS ids
RETURN p.id AS id, p.name AS name, p.role AS role, n, ids[0..5] AS evidence
ORDER BY n DESC, id
LIMIT $limit
UNION ALL
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(i:Issue)<-[:FIXES]-(pr:PR)<-[:AUTHORED]-(p:Person)
WHERE $metric = 'bugs_fixed'
WITH p, count(DISTINCT i) AS n, collect(DISTINCT pr.id) AS ids
RETURN p.id AS id, p.name AS name, p.role AS role, n, ids[0..5] AS evidence
ORDER BY n DESC, id
LIMIT $limit
UNION ALL
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(i:Issue)-[a:ASSIGNED_TO]->(p:Person)
WHERE $metric = 'open_bugs_assigned' AND a.to_ms IS NULL AND i.current_status IN ['reported', 'reopened']
WITH p, count(DISTINCT i) AS n, collect(DISTINCT i.id) AS ids
RETURN p.id AS id, p.name AS name, p.role AS role, n, ids[0..5] AS evidence
ORDER BY n DESC, id
LIMIT $limit
