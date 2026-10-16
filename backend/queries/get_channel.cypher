// get_channel.cypher — one channel by id: the live path checks that a new message's channel exists before writing it (11.6)
MATCH (c:Channel {id: $id})
RETURN c.id AS id, c.name AS name
LIMIT 1
