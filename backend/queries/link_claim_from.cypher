// link_claim_from.cypher — (Claim)-[:FROM {quote, start, end}]->(Artifact): the message a claim's verbatim quote comes from (S6)
UNWIND $rows AS r
MATCH (c:Claim {id: r.claim_id})
MATCH (a:Artifact {id: r.artifact_id})
MERGE (c)-[e:FROM]->(a)
SET e.quote = r.quote, e.start = r.start, e.end = r.end
