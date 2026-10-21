// get_channel.cypher — one channel by id and its kind: the live path checks that a new message's channel exists before writing it (11.6), and reads what to extract from it (D116)
MATCH (c:Channel {id: $id})
RETURN c.id AS id, c.name AS name, c.kind AS kind
LIMIT 1
