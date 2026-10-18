// link_authored.cypher — (Person)-[:AUTHORED]->(Artifact)
UNWIND $rows AS r
MATCH (p:Person {id: r.person_id})
MATCH (a:Artifact {id: r.artifact_id})
MERGE (p)-[:AUTHORED]->(a)
