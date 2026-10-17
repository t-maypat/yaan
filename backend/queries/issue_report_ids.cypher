// issue_report_ids.cypher — the messages that reported one issue, oldest first, so a connector can find the ticket it came from (17.5 write-back)
MATCH (:Issue {id: $issue_id})-[:REPORTED_IN]->(m:Artifact)
RETURN m.id AS id, m.ts_ms AS ts_ms
ORDER BY m.ts_ms
LIMIT $limit
