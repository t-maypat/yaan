// link_decided_by.cypher — (Decision)-[:DECIDED_BY]->(Person)
UNWIND $rows AS r
MATCH (d:Decision {id: r.decision_id})
MATCH (p:Person {id: r.person_id})
MERGE (d)-[:DECIDED_BY]->(p)
