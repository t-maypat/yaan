// mark_action_sent.cypher — record where an approved action was delivered (a URL in the connected tool) and when, so it is sent once
UNWIND $rows AS r
MATCH (a:Action {id: r.id})
SET a.sent_ref = r.ref, a.sent_ms = r.ms, a.sent_via = r.via
