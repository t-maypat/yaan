// get_alert.cypher — one alert with its facts, and the person who would respond to it (approver), before an approve or dismiss (11.4)
MATCH (a:Alert {id: $alert_id})
OPTIONAL MATCH (p:Person {id: $person_id})
RETURN a.id AS id, a.kind AS kind, a.status AS status, a.change AS change, a.summary AS summary,
       a.decision_id AS decision_id, a.subject_id AS subject_id, a.subject_title AS subject_title,
       a.product_id AS product_id, a.artifact_id AS artifact_id, a.quote AS quote,
       a.response_id AS response_id, p.id AS person_id, p.name AS person_name, p.role AS person_role,
       p.authority AS person_authority
LIMIT 1
