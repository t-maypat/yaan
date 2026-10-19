// fold_product_prs.cypher — a product's pull requests with author and reviewers: PR references resolve to them and the policy reads them (the fold's context, D101)
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(pr:PR)
OPTIONAL MATCH (au:Person)-[:AUTHORED]->(pr)
WITH pr, au
OPTIONAL MATCH (rv:Person)-[:REVIEWED]->(pr)
WITH pr, au, collect(DISTINCT rv.id) AS reviewers
RETURN pr.id AS id, pr.repo AS repo, pr.number AS number, pr.external AS external,
       pr.ts_ms AS ts_ms, pr.ts_iso AS ts_iso, au.id AS author, reviewers
LIMIT $limit
