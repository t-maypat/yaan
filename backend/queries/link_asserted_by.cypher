// link_asserted_by.cypher — (Claim)-[:ASSERTED_BY]->(Person): who said it (the message's sender)
UNWIND $rows AS r
MATCH (c:Claim {id: r.claim_id})
MATCH (p:Person {id: r.person_id})
MERGE (c)-[:ASSERTED_BY]->(p)
