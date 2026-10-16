// link_claim_about_pr.cypher — (Claim)-[:ABOUT]->(PR): the pull request a PR-intent claim is about
UNWIND $rows AS r
MATCH (c:Claim {id: r.claim_id})
MATCH (a:Artifact {id: r.pr_id})
MERGE (c)-[:ABOUT]->(a)
