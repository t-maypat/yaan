// link_member_of.cypher — (Person)-[:MEMBER_OF]->(Team)
UNWIND $rows AS r
MATCH (p:Person {id: r.person_id})
MATCH (t:Team {id: r.team_id})
MERGE (p)-[:MEMBER_OF]->(t)
