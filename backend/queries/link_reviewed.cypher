// link_reviewed.cypher — (Person)-[:REVIEWED {state, ts_ms, comment}]->(PR), one edge per review
UNWIND $rows AS r
MATCH (p:Person {id: r.person_id})
MATCH (a:Artifact {id: r.artifact_id})
MERGE (p)-[e:REVIEWED {ts_ms: r.ts_ms}]->(a)
SET e.state = r.state, e.ts_iso = r.ts_iso, e.comment = r.comment
