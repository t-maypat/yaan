// area_owners.cypher — the listed owners of the given areas: who the area's issues are assigned to, with each issue's status and its report message (where the assignment is asked, to cite), to compare with who actually knows it (10.7)
UNWIND $ids AS id
MATCH (a:Area {id: id})<-[:IN_AREA]-(i:Issue)-[:ASSIGNED_TO]->(p:Person)
OPTIONAL MATCH (i)-[:REPORTED_IN]->(m:Artifact)
WITH a, i, p, m
ORDER BY m.ts_ms
WITH a, i, p, collect(m.id)[0] AS report_id, collect(m.text)[0] AS report_text
RETURN a.id AS area_id, i.id AS issue_id, i.title AS title, i.current_status AS status,
       p.id AS id, p.name AS name, p.role AS role, report_id, report_text
LIMIT $limit
