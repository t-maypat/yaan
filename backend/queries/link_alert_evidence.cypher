// link_alert_evidence.cypher — (Alert)-[:EVIDENCED_BY {quote}]->(Artifact): the message that triggered the alert, with its verbatim quote
UNWIND $rows AS r
MATCH (a:Alert {id: r.alert_id})
MATCH (m:Artifact {id: r.artifact_id})
MERGE (a)-[e:EVIDENCED_BY]->(m)
SET e.quote = r.quote
