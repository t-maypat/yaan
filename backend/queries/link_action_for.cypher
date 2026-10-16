// link_action_for.cypher — (Action)-[:FOR]->(Alert): the alert a draft answers
UNWIND $rows AS r
MATCH (a:Action {id: r.id})
MATCH (al:Alert {id: r.alert_id})
MERGE (a)-[:FOR]->(al)
