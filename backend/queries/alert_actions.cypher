// alert_actions.cypher — the drafted actions for one alert, for the Inbox: ticket comment first, then the correction, then notifications
MATCH (:Alert {id: $alert_id})<-[:FOR]-(a:Action)
RETURN a.id AS id, a.kind AS kind, a.status AS status, a.target_kind AS target_kind,
       a.target_id AS target_id, a.target_name AS target_name, a.draft AS draft,
       a.quote AS quote, a.artifact_id AS artifact_id
ORDER BY CASE a.kind WHEN 'ticket_comment' THEN 0 WHEN 'doc_fix' THEN 1 ELSE 2 END, a.target_name
LIMIT $limit
