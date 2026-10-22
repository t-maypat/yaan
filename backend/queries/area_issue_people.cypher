// area_issue_people.cypher — who worked on each issue and how (10.2): `fix` = authored a PR that FIXES it, `resolved` = stated its resolution, `talk` = any other claim about it; `source_id`: the PR or claim it rests on, so org mode can keep only open ones (D131)
MATCH (pr:PR)-[:FIXES]->(i:Issue)
MATCH (p:Person)-[:AUTHORED]->(pr)
RETURN i.id AS issue_id, p.id AS person_id, 'fix' AS how, pr.id AS ref_id, pr.ts_ms AS ts_ms,
       pr.id AS source_id
LIMIT $limit
UNION ALL
MATCH (c:Claim)-[:ABOUT]->(i:Issue)
MATCH (c)-[:ASSERTED_BY]->(p:Person)
RETURN i.id AS issue_id, p.id AS person_id, CASE WHEN c.value = 'resolved' THEN 'resolved' ELSE 'talk' END AS how, i.id AS ref_id, c.ts_ms AS ts_ms,
       c.id AS source_id
LIMIT $limit
