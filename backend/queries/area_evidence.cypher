// area_evidence.cypher — the pull requests each person authored in the given areas: the quotable evidence behind an expertise claim (10.7)
UNWIND $ids AS id
MATCH (a:Area {id: id})<-[:IN_AREA]-(pr:PR)<-[:AUTHORED]-(p:Person)
RETURN a.id AS area_id, p.id AS person_id, pr.id AS artifact_id, pr.number AS number,
       pr.title AS title, pr.ts_iso AS ts_iso
ORDER BY pr.ts_ms
LIMIT $limit
