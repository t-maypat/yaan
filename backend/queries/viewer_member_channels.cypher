// viewer_member_channels.cypher — the channels a person is a member of (org mode: from Slack or connectors.yaml), for viewer-scoped answers (H1, D109)
MATCH (:Person {id: $viewer})-[:MEMBER_OF]->(c:Channel)
RETURN DISTINCT c.id AS id
LIMIT $limit
