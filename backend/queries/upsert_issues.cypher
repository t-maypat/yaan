// upsert_issues.cypher — canonical Issue nodes from extracted reports, keyed by normalized title (S7, D049); status from the fold
UNWIND $rows AS r
MERGE (i:Issue:Entity {id: r.id})
SET i.name = r.title, i.title = r.title, i.product_id = r.product_id, i.component = r.component,
    i.description = r.description, i.aliases = r.aliases, i.reported_ms = r.reported_ms,
    i.reported_iso = r.reported_iso, i.current_status = r.current_status,
    i.current_assignee = r.current_assignee, i.status_since_ms = r.status_since_ms,
    i.reopen_count = r.reopen_count, i.first_seen = r.first_seen
