// derive_works_on.cypher — (Team)-[:WORKS_ON {stage, from_ms, to_ms, artifacts}]->(Product): when each team authored the product's artifacts per stage (channel stage, else artifact kind) (S9, 8.0 / R1-2)
MATCH (p:Product)<-[:IN_PRODUCT]-(a:Artifact)<-[:AUTHORED]-(:Person)-[:MEMBER_OF]->(t:Team)
OPTIONAL MATCH (a)-[:IN_CHANNEL]->(ch:Channel)
WITH p, t, coalesce(ch.stage, a.kind) AS stage, min(a.ts_ms) AS from_ms, max(a.ts_ms) AS to_ms, count(a) AS n
WHERE n >= $min_artifacts
MERGE (t)-[w:WORKS_ON {stage: stage}]->(p)
SET w.from_ms = from_ms, w.to_ms = to_ms, w.artifacts = n
