// link_affects_issue.cypher — (Decision)-[:AFFECTS]->(Issue): what an issue-status decision is about
UNWIND $rows AS r
MATCH (d:Decision {id: r.decision_id})
MATCH (i:Issue {id: r.issue_id})
MERGE (d)-[:AFFECTS]->(i)
