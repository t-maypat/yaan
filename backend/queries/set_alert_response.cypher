// set_alert_response.cypher — close an open alert as approved or dismissed, with who and when (11.4); matches nothing when the alert is no longer open, so a second response is refused
UNWIND $rows AS r
MATCH (a:Alert {id: r.alert_id})
WHERE a.status = 'open'
SET a.status = r.status, a.decided_ms = r.decided_ms, a.decided_iso = r.decided_iso,
    a.decided_by = r.person_id, a.response_id = r.decision_id, a.note = r.note
RETURN a.id AS id
