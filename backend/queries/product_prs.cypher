// product_prs.cypher — the product's own pull requests (external ones left out) with their status chain, author, reviews, and the PRs that revert them or that they revert
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(pr:PR)
WHERE coalesce(pr.external, false) = false AND ($status IS NULL OR pr.current_status = $status)
OPTIONAL MATCH (au:Person)-[:AUTHORED]->(pr)
OPTIONAL MATCH (rv:Person)-[r:REVIEWED]->(pr)
WITH pr, collect(DISTINCT au.id) AS authors, collect(DISTINCT {id: rv.id, state: r.state}) AS reviews
OPTIONAL MATCH (undo:PR)-[:REVERTS]->(pr)
WITH pr, authors, reviews,
     collect(DISTINCT {number: undo.number, url: undo.url, title: undo.title, reason: undo.text}) AS reverted_by
OPTIONAL MATCH (pr)-[:REVERTS]->(orig:PR)
WITH pr, authors, reviews, reverted_by, collect(DISTINCT orig.number) AS reverts
OPTIONAL MATCH (d:Decision)-[:AFFECTS]->(pr)
WITH pr, authors, reviews, reverted_by, reverts, d
ORDER BY d.valid_from_ms
WITH pr, authors, reviews, reverted_by, reverts, collect(d.value) AS chain
ORDER BY pr.number
RETURN pr.id AS id, pr.number AS number, pr.title AS title, pr.url AS url,
       pr.current_status AS status, chain, authors, reviews, reverted_by, reverts
LIMIT $limit
