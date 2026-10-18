// link_spoke_in.cypher — (Person)-[:SPOKE_IN {turns, method}]->(Meeting or MeetingChat), speaker names resolved (S4, D037)
UNWIND $rows AS r
MATCH (p:Person {id: r.person_id})
MATCH (a:Artifact {id: r.artifact_id})
MERGE (p)-[s:SPOKE_IN]->(a)
SET s.turns = r.turns, s.method = r.method
