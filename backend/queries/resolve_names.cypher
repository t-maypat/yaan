// resolve_names.cypher — people, products, customers or companies whose name matches the words (full-text on Entity.name), most active in the product first
CALL db.idx.fulltext.queryNodes('Entity', $q) YIELD node, score
OPTIONAL MATCH (node)-[:AUTHORED|SPOKE_IN|ATTENDED]->(a:Artifact)-[:IN_PRODUCT]->(:Product {id: $product_id})
OPTIONAL MATCH (node)-[:WORKS_AT]->(co:Company)
WITH node, score, co, count(DISTINCT a) AS activity
ORDER BY activity DESC, score DESC
RETURN node.id AS id, node.name AS name, labels(node) AS labels, node.role AS role,
       co.name AS company, activity, score
LIMIT $k
