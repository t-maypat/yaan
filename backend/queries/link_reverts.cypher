// link_reverts.cypher — (PR)-[:REVERTS]->(PR): a "Revert PR# N" pull request and the PR N it undoes, in the same repository (S4, D047)
UNWIND $rows AS r
MATCH (rev:Artifact {id: r.revert_id})
MATCH (orig:Artifact {id: r.original_id})
MERGE (rev)-[:REVERTS]->(orig)
