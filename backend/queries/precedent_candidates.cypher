// precedent_candidates.cypher — every extracted decision that isn't a plain report, with what it is about (issue title + description, or PR title): the input for PRECEDENT_FOR by similarity (10.6, D066)
MATCH (d:Decision {origin: 'extracted'})-[:AFFECTS]->(s)
WHERE d.value <> 'reported'
RETURN d.id AS id, d.kind AS kind, d.value AS value, d.valid_from_ms AS ts_ms, s.id AS subject_id,
       coalesce(s.title, s.name, s.id) + '. ' + coalesce(s.description, s.text, '') AS text,
       coalesce(s.product_id, '') AS product_id
LIMIT $limit
