// viewer_meetings.cypher — the meetings a person attended or spoke in, for viewer-scoped answers (W7)
MATCH (:Person {id: $viewer})-[:ATTENDED|SPOKE_IN]->(m:Artifact)
RETURN DISTINCT m.id AS id
LIMIT $limit
