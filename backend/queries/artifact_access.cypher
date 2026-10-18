// artifact_access.cypher — what the permission policy needs per artifact: its kind and its channel, for viewer-scoped answers (W7)
UNWIND $ids AS id
MATCH (a:Artifact {id: id})
OPTIONAL MATCH (a)-[:IN_CHANNEL]->(c:Channel)
RETURN a.id AS id, a.kind AS kind, c.id AS channel_id, c.name AS channel
LIMIT $limit
