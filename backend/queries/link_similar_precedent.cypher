// link_similar_precedent.cypher — (earlier Decision)-[:PRECEDENT_FOR {basis, similarity}]->(later Decision): the same kind of decision, taken earlier, on a similar issue or PR (10.6, D066)
UNWIND $rows AS r
MATCH (a:Decision {id: r.precedent_id})
MATCH (b:Decision {id: r.decision_id})
MERGE (a)-[e:PRECEDENT_FOR {basis: 'similar_subject'}]->(b)
SET e.similarity = r.similarity, e.run = r.run
