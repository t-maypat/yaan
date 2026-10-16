// link_fixes.cypher — (PR)-[:FIXES]->(Issue): the pull request a resolution message points to
UNWIND $rows AS r
MATCH (a:Artifact {id: r.pr_id})
MATCH (i:Issue {id: r.issue_id})
MERGE (a)-[:FIXES]->(i)
