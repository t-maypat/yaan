// link_attended.cypher — (Person)-[:ATTENDED]->(Meeting) from participants
UNWIND $rows AS r
MATCH (p:Person {id: r.person_id})
MATCH (a:Artifact {id: r.artifact_id})
MERGE (p)-[:ATTENDED]->(a)
