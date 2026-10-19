// fold_product_active.cypher — the people who authored or reviewed anything in a product: "works on the product" for the policy (the fold's context, D101)
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(a:Artifact)
WITH a
MATCH (p:Person)-[:AUTHORED|REVIEWED]->(a)
RETURN DISTINCT p.id AS id
LIMIT $limit
