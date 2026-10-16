// list_alerts.cypher — the Inbox: alerts, newest change first, optionally by status and product, with who they notify
MATCH (a:Alert)
WHERE ($status IS NULL OR a.status = $status) AND ($product_id IS NULL OR a.product_id = $product_id)
OPTIONAL MATCH (a)-[n:NOTIFIES]->(p:Person)
WITH a, collect({id: p.id, name: p.name, role: p.role, reasons: n.reasons}) AS notifies
ORDER BY a.ts_ms DESC
RETURN a.id AS id, a.kind AS kind, a.severity AS severity, a.status AS status, a.summary AS summary,
       a.decision_id AS decision_id, a.subject_id AS subject_id, a.subject_title AS subject_title,
       a.product_id AS product_id, a.from_value AS from_value, a.to_value AS to_value,
       a.ts_ms AS ts_ms, a.ts_iso AS ts_iso, a.artifact_id AS artifact_id, a.quote AS quote,
       a.actor_id AS actor_id, a.actor_name AS actor_name, a.actor_role AS actor_role,
       a.reason AS reason, a.radius_size AS radius_size, a.created_iso AS created_iso, notifies
LIMIT $limit
