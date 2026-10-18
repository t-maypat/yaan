// product_prs.cypher — the product's own pull requests (external ones left out) with their status chain, author and reviews
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(pr:PR)
WHERE coalesce(pr.external, false) = false AND ($status IS NULL OR pr.current_status = $status)
OPTIONAL MATCH (au:Person)-[:AUTHORED]->(pr)
OPTIONAL MATCH (rv:Person)-[r:REVIEWED]->(pr)
WITH pr, collect(DISTINCT au.id) AS authors, collect(DISTINCT {id: rv.id, state: r.state}) AS reviews
OPTIONAL MATCH (d:Decision)-[:AFFECTS]->(pr)
WITH pr, authors, reviews, d
ORDER BY d.valid_from_ms
WITH pr, authors, reviews, collect(d.value) AS chain
ORDER BY pr.number
RETURN pr.id AS id, pr.number AS number, pr.title AS title, pr.url AS url,
       pr.current_status AS status, chain, authors, reviews
LIMIT $limit
