// artifact_access.cypher — what the permission policy needs per artifact: its kind, its channel and that channel's visibility, for viewer-scoped answers (W7, D109); a PR's repository, for synced GitHub readers (D131)
UNWIND $ids AS id
MATCH (a:Artifact {id: id})
OPTIONAL MATCH (a)-[:IN_CHANNEL]->(c:Channel)
RETURN a.id AS id, a.kind AS kind, c.id AS channel_id, c.name AS channel,
       c.visibility AS visibility, a.repo AS repo
LIMIT $limit
