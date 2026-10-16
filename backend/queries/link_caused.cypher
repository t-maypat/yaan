// link_caused.cypher — (Decision)-[:CAUSED]->(Decision): the change that led to a response decision (11.4)
UNWIND $rows AS r
MATCH (a:Decision {id: r.cause_id})
MATCH (b:Decision {id: r.decision_id})
MERGE (a)-[:CAUSED]->(b)
