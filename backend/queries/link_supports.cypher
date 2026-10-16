// link_supports.cypher — (Claim)-[:SUPPORTS]->(Decision): a claim that created or repeated the decision's value (§9.4)
UNWIND $rows AS r
MATCH (c:Claim {id: r.claim_id})
MATCH (d:Decision {id: r.decision_id})
MERGE (c)-[:SUPPORTS]->(d)
