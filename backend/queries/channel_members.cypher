// channel_members.cypher — the people a channel lists as members (org mode), so a membership sync changes only what changed (H1, D109)
UNWIND $ids AS id
MATCH (p:Person)-[:MEMBER_OF]->(c:Channel {id: id})
RETURN c.id AS channel_id, p.id AS person_id
LIMIT $limit
