// link_mentions_company.cypher — (Artifact)-[:MENTIONS]->(Company) by company name (S4)
UNWIND $rows AS r
MATCH (a:Artifact {id: r.artifact_id})
MATCH (e:Company {id: r.entity_id})
MERGE (a)-[:MENTIONS]->(e)
