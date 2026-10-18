// people_by_role.cypher — people with a role (from the org chart) who authored, spoke in or attended the product's artifacts, most active first
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(a:Artifact)<-[:AUTHORED|SPOKE_IN|ATTENDED]-(p:Person)
WHERE toLower(p.role) = toLower($role)
WITH p, count(DISTINCT a) AS activity
ORDER BY activity DESC
RETURN p.id AS id, p.name AS name, p.role AS role, activity
LIMIT $k
