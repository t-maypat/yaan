// link_evidenced_by.cypher — (Decision)-[:EVIDENCED_BY {quote, field}]->(Artifact), the source of a decision
UNWIND $rows AS r
MATCH (d:Decision {id: r.decision_id})
MATCH (a:Artifact {id: r.artifact_id})
MERGE (d)-[e:EVIDENCED_BY {field: r.field}]->(a)
SET e.quote = r.quote, e.quote_hash = r.quote_hash
