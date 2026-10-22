// forget_targets.cypher — the artifacts `forget` removes: each one's kind and the products its fold must be rebuilt for (F2, D125)
MATCH (a:Artifact)
WHERE a.id IN $ids
OPTIONAL MATCH (a)-[:IN_PRODUCT]->(p:Product)
WITH a, collect(p.id) AS products
RETURN a.id AS id, coalesce(a.kind, 'artifact') AS kind, products
LIMIT $limit
