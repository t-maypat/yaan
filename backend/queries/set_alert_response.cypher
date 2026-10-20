// set_alert_response.cypher — the last step of a response: a 'responding' alert closes as approved or dismissed, with who and when (11.4, D110); matches nothing unless the response was claimed, so a second response is refused
UNWIND $rows AS r
MATCH (a:Alert {id: r.alert_id})
WHERE a.status = 'responding'
SET a.status = r.status, a.decided_ms = r.decided_ms, a.decided_iso = r.decided_iso,
    a.decided_by = r.person_id, a.response_id = r.decision_id, a.note = r.note,
    a.response_intent = null
RETURN a.id AS id
