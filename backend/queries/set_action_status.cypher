// set_action_status.cypher — record what happened to each drafted action (approved, skipped, dismissed), with the edited text when the approver changed it
UNWIND $rows AS r
MATCH (a:Action {id: r.id})
SET a.status = r.status, a.decided_ms = r.decided_ms, a.decided_by = r.person_id,
    a.draft = coalesce(r.draft, a.draft)
