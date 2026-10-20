// stuck_responses.cypher — responses claimed but never closed (the process stopped midway), with the writes they still owe (H2, D110)
MATCH (a:Alert {status: 'responding'})
WHERE a.response_started_ms < $before_ms
RETURN a.id AS id, a.response_intent AS intent
ORDER BY a.response_started_ms
LIMIT $limit
