// link_assigned_to.cypher — (Issue)-[:ASSIGNED_TO {from_ms, to_ms}]->(Person): who was asked to take the issue, and when
UNWIND $rows AS r
MATCH (i:Issue {id: r.issue_id})
MATCH (p:Person {id: r.person_id})
MERGE (i)-[e:ASSIGNED_TO]->(p)
SET e.from_ms = r.from_ms, e.to_ms = r.to_ms
