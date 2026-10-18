// links_shared.cypher — links shared in the product's messages, with who shared them and where
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(l:Link)
OPTIONAL MATCH (m:Message)-[:SHARES]->(l)
WHERE (m)-[:IN_PRODUCT]->(:Product {id: $product_id})
WITH l, m
OPTIONAL MATCH (p:Person)-[:AUTHORED]->(m)
WITH l, collect(DISTINCT p.id) AS shared_by, collect(DISTINCT m.id) AS shared_in
ORDER BY l.ts_ms
RETURN l.id AS id, l.url AS url, l.title AS title, l.text AS description, shared_by, shared_in
LIMIT $limit
