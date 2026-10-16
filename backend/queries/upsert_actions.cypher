// upsert_actions.cypher — drafted follow-ups for an alert (11.3): ticket_comment, doc_fix, notify_owner; a new draft starts proposed, and a re-run rewrites the text only while it is still proposed
UNWIND $rows AS r
MERGE (a:Action {id: r.id})
ON CREATE SET a.status = 'proposed', a.created_ms = r.observed_ms
SET a.kind = r.kind, a.alert_id = r.alert_id, a.target_kind = r.target_kind,
    a.target_id = r.target_id, a.target_name = r.target_name, a.quote = r.quote,
    a.artifact_id = r.artifact_id,
    a.draft = CASE WHEN a.status = 'proposed' THEN r.draft ELSE a.draft END
