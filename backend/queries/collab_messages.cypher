// collab_messages.cypher — who wrote each (non-system) Slack message and in which channel: the conversation part of COLLAB (10.1)
MATCH (p:Person)-[:AUTHORED]->(m:Message)
WHERE coalesce(m.system, false) = false
RETURN m.id AS message_id, m.channel_id AS channel_id, p.id AS person_id
LIMIT $limit
