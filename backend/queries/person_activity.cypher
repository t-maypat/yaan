// person_activity.cypher — what one person did per product: artifacts authored, reviewed, attended or spoken in, by kind (tool 4 person_profile, R2)
MATCH (p:Person {id: $id})-[r:AUTHORED|REVIEWED|ATTENDED|SPOKE_IN]->(a:Artifact)-[:IN_PRODUCT]->(pr:Product)
WITH pr, type(r) AS relation, coalesce(a.kind, 'artifact') AS kind, count(DISTINCT a) AS n
RETURN pr.id AS product_id, relation, kind, n
ORDER BY n DESC
LIMIT $limit
