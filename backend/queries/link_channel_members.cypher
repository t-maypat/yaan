// link_channel_members.cypher — (Person)-[:MEMBER_OF]->(Channel): who may read a private channel in org mode (H1, D109)
UNWIND $rows AS r
MATCH (p:Person {id: r.person_id})
MATCH (c:Channel {id: r.channel_id})
MERGE (p)-[m:MEMBER_OF]->(c)
SET m.since_ms = coalesce(m.since_ms, r.now_ms)
RETURN count(m) AS n
