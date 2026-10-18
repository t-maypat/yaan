// link_in_channel.cypher — (Artifact)-[:IN_CHANNEL]->(Channel)
UNWIND $rows AS r
MATCH (a:Artifact {id: r.artifact_id})
MATCH (c:Channel {id: r.channel_id})
MERGE (a)-[:IN_CHANNEL]->(c)
