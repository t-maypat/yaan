// link_alert_caused.cypher — re-link CAUSED from each answered alert's flagged decision to its response after a fold rebuild (the fold recreates the flagged decision under the same id)
MATCH (a:Alert)
WHERE a.response_id IS NOT NULL
WITH a
MATCH (d:Decision {id: a.decision_id})
MATCH (r:Decision {id: a.response_id})
MERGE (d)-[:CAUSED]->(r)
