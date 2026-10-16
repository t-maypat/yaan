// link_claim_about_issue.cypher — (Claim)-[:ABOUT]->(Issue): the issue a lifecycle claim is about
UNWIND $rows AS r
MATCH (c:Claim {id: r.claim_id})
MATCH (i:Issue {id: r.issue_id})
MERGE (c)-[:ABOUT]->(i)
