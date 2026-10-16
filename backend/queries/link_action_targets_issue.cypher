// link_action_targets_issue.cypher — (Action)-[:TARGETS]->(Issue): the issue a ticket comment goes on
UNWIND $rows AS r
MATCH (a:Action {id: r.id})
MATCH (i:Issue {id: r.target_id})
MERGE (a)-[:TARGETS]->(i)
