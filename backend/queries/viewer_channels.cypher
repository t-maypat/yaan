// viewer_channels.cypher — the channels a person belongs to (they posted there), for viewer-scoped answers (W7)
MATCH (:Person {id: $viewer})-[:AUTHORED]->(:Message)-[:IN_CHANNEL]->(c:Channel)
RETURN DISTINCT c.id AS id
LIMIT $limit
