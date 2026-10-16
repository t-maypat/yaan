// link_alert_flags_claim.cypher — (Alert)-[:FLAGS]->(Claim): the conflicting claim behind a conflict alert, re-linked after every fold rebuild (claim ids are stable)
UNWIND $rows AS r
MATCH (a:Alert {id: r.alert_id})
MATCH (c:Claim {id: r.claim_id})
MERGE (a)-[:FLAGS]->(c)
