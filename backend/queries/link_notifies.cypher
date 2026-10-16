// link_notifies.cypher — (Alert)-[:NOTIFIES {reasons}]->(Person): the people in the alert's blast radius, with why each one is in it
UNWIND $rows AS r
MATCH (a:Alert {id: r.alert_id})
MATCH (p:Person {id: r.person_id})
MERGE (a)-[e:NOTIFIES]->(p)
SET e.reasons = r.reasons
