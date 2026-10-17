// collab_meetings.cypher — who attended each meeting: the co-attendance part of COLLAB (10.1)
MATCH (p:Person)-[:ATTENDED]->(m:Meeting)
RETURN m.id AS meeting_id, p.id AS person_id
LIMIT $limit
