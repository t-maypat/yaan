// link_conflicts_with.cypher — (Claim)-[:CONFLICTS_WITH {reason}]->(Decision): an unauthorized claim that did not change state (§9.4, R1-3)
UNWIND $rows AS r
MATCH (c:Claim {id: r.claim_id})
MATCH (d:Decision {id: r.decision_id})
MERGE (c)-[e:CONFLICTS_WITH]->(d)
SET e.reason = r.reason
