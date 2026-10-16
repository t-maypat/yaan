// upsert_alerts.cypher — Sentinel Alert nodes, one per alert-worthy decision change (11.1); a new alert starts open, and a re-run never resets its status or created time
UNWIND $rows AS r
MERGE (a:Alert {id: r.id})
ON CREATE SET a.status = 'open', a.created_ms = r.observed_ms, a.created_iso = r.observed_iso
SET a.kind = r.kind, a.severity = r.severity, a.change = r.change, a.summary = r.summary,
    a.decision_id = r.decision_id, a.trigger_id = r.trigger_id, a.topic = r.topic,
    a.subject_id = r.subject_id, a.subject_title = r.subject_title, a.product_id = r.product_id,
    a.from_value = r.from_value, a.to_value = r.to_value, a.from_ms = r.from_ms,
    a.ts_ms = r.ts_ms, a.ts_iso = r.ts_iso, a.artifact_id = r.artifact_id, a.quote = r.quote,
    a.actor_id = r.actor_id, a.actor_name = r.actor_name, a.actor_role = r.actor_role,
    a.reason = r.reason, a.radius_size = r.radius_size, a.radius_people = r.radius_people,
    a.radius_companies = r.radius_companies, a.seen_ms = r.observed_ms,
    a.status = CASE WHEN a.status = 'withdrawn' THEN 'open' ELSE a.status END
