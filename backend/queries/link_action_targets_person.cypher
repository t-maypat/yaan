// link_action_targets_person.cypher — (Action)-[:TARGETS]->(Person): who a notification goes to
UNWIND $rows AS r
MATCH (a:Action {id: r.id})
MATCH (p:Person {id: r.target_id})
MERGE (a)-[:TARGETS]->(p)
