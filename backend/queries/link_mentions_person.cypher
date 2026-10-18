// link_mentions_person.cypher — (Artifact)-[:MENTIONS]->(Person) from @eid_ mentions (S4)
UNWIND $rows AS r
MATCH (a:Artifact {id: r.artifact_id})
MATCH (e:Person {id: r.entity_id})
MERGE (a)-[:MENTIONS]->(e)
