// link_affects.cypher — (Decision)-[:AFFECTS]->(Artifact), what a structural decision is about
UNWIND $rows AS r
MATCH (d:Decision {id: r.decision_id})
MATCH (a:Artifact {id: r.artifact_id})
MERGE (d)-[:AFFECTS]->(a)
