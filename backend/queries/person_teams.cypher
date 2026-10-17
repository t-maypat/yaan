// person_teams.cypher — each person's team from the org chart (MEMBER_OF), to compare with the working groups (10.4, D064)
MATCH (p:Person)-[:MEMBER_OF]->(t:Team)
RETURN p.id AS person_id, t.id AS team_id, t.name AS team
LIMIT $limit
