// channel_access.cypher — every channel with its name and visibility: what a viewer may read, computed once, so search filters before its limit (D131)
MATCH (c:Channel)
RETURN c.id AS id, c.name AS name, c.visibility AS visibility
LIMIT $limit
