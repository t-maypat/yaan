// forget_actions.cypher — drafted actions that quote, target or answer a forgotten item lose their text; one not yet sent is withdrawn, so nothing quoting it leaves Yaan (F2, D125)
MATCH (x:Action)
WHERE x.artifact_id IN $ids OR x.target_id IN $ids OR x.alert_id IN $alert_ids
SET x.draft = null, x.quote = null, x.target_name = null, x.forgotten_ms = $now_ms,
    x.status = CASE WHEN x.sent_ref IS NULL AND x.status IN ['proposed', 'approved'] THEN 'withdrawn' ELSE x.status END
RETURN x.id AS id
