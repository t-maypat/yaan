// link_shares.cypher — (Artifact)-[:SHARES {url}]->(Artifact): a PR, doc or link whose url appears in the text (S4)
UNWIND $rows AS r
MATCH (a:Artifact {id: r.artifact_id})
MATCH (t:Artifact {id: r.target_id})
MERGE (a)-[s:SHARES]->(t)
SET s.url = r.url
