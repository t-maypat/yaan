// area_owners.cypher — the listed owners of the given areas: who the area's issues are assigned to, with each issue's status, to compare with who actually knows it (10.7)
UNWIND $ids AS id
MATCH (a:Area {id: id})<-[:IN_AREA]-(i:Issue)-[:ASSIGNED_TO]->(p:Person)
RETURN a.id AS area_id, i.id AS issue_id, i.title AS title, i.current_status AS status,
       p.id AS id, p.name AS name, p.role AS role
LIMIT $limit
