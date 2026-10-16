// list_products.cypher — every product with its codenames and how many artifacts it holds, for the UI's product picker
MATCH (p:Product)
OPTIONAL MATCH (p)<-[:IN_PRODUCT]-(a:Artifact)
WITH p, count(a) AS artifacts
RETURN p.id AS id, coalesce(p.name, p.id) AS name, coalesce(p.aliases, []) AS aliases, artifacts
ORDER BY id
LIMIT $limit
