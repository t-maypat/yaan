// link_mentions_customer.cypher — (Artifact)-[:MENTIONS]->(Customer) by CUST id or unambiguous name (S4)
UNWIND $rows AS r
MATCH (a:Artifact {id: r.artifact_id})
MATCH (e:Customer {id: r.entity_id})
MERGE (a)-[:MENTIONS]->(e)
