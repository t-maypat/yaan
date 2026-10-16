// link_action_targets_artifact.cypher — (Action)-[:TARGETS]->(Artifact): the PR a comment goes on, or the message a correction goes under
UNWIND $rows AS r
MATCH (a:Action {id: r.id})
MATCH (t:Artifact {id: r.target_id})
MERGE (a)-[:TARGETS]->(t)
