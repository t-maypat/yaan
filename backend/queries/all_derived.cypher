// all_derived.cypher — every derived node (claims, issues, extracted decisions), for the one-time rewrite of a graph whose derived layer predates D101
MATCH (c:Claim)
RETURN c.id AS id, 'Claim' AS label
LIMIT $limit
UNION ALL
MATCH (i:Issue)
RETURN i.id AS id, 'Issue' AS label
LIMIT $limit
UNION ALL
MATCH (d:Decision)
WHERE d.origin = 'extracted'
RETURN d.id AS id, 'Decision' AS label
LIMIT $limit
