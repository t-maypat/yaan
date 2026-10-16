// link_reported_in.cypher — (Issue)-[:REPORTED_IN]->(Artifact): the message that raised the issue
UNWIND $rows AS r
MATCH (i:Issue {id: r.issue_id})
MATCH (a:Artifact {id: r.artifact_id})
MERGE (i)-[:REPORTED_IN]->(a)
