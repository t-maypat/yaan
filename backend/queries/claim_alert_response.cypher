// claim_alert_response.cypher — the first step of a response: an open alert becomes 'responding' and stores every write the response will make, so a crash midway is finished, not left half-done (H2, D110); matches nothing when the alert isn't open
UNWIND $rows AS r
MATCH (a:Alert {id: r.alert_id})
WHERE a.status = 'open'
SET a.status = 'responding', a.response_intent = r.intent, a.response_started_ms = r.now_ms
RETURN a.id AS id
