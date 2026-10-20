// actions_to_send.cypher — approved drafted actions not yet sent to a connected tool, of responses that finished (D110), oldest decision first, with what they target (17.2 write-back)
MATCH (a:Action {status: 'approved'})-[:FOR]->(al:Alert {status: 'approved'})
WHERE a.sent_ref IS NULL
RETURN a.id AS id, a.kind AS kind, a.target_kind AS target_kind, a.target_id AS target_id,
       a.target_name AS target_name, a.draft AS draft, a.artifact_id AS artifact_id,
       a.decided_by AS decided_by, a.decided_ms AS decided_ms
ORDER BY a.decided_ms, a.id
LIMIT $limit
