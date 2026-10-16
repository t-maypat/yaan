// link_alert_flags.cypher — (Alert)-[:FLAGS]->(Decision): the decision whose change the alert is about, re-linked after every fold rebuild (decision ids are stable)
UNWIND $rows AS r
MATCH (a:Alert {id: r.alert_id})
MATCH (d:Decision {id: r.decision_id})
MERGE (a)-[:FLAGS]->(d)
