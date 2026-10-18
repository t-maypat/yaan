// artifact_people_detail.cypher — the people around one artifact: authors, attendees, speakers, reviewers (with review state) and people it mentions
MATCH (p:Person)-[r:AUTHORED|ATTENDED|SPOKE_IN|REVIEWED]->(:Artifact {id: $id})
RETURN p.id AS id, p.name AS name, p.role AS role, type(r) AS relation, r.state AS state
LIMIT 100
UNION ALL
MATCH (:Artifact {id: $id})-[:MENTIONS]->(p:Person)
RETURN p.id AS id, p.name AS name, p.role AS role, 'MENTIONS' AS relation, null AS state
LIMIT 100
