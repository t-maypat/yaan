// link_precedent_for.cypher — (Decision)-[:PRECEDENT_FOR]->(Decision): an earlier response to the same kind of alert, from older to newer (11.4)
UNWIND $rows AS r
MATCH (a:Decision {id: r.precedent_id})
MATCH (b:Decision {id: r.decision_id})
MERGE (a)-[:PRECEDENT_FOR]->(b)
