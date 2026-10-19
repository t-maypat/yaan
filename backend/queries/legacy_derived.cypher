// legacy_derived.cypher — extracted decisions written before D101 carry no product_id: a graph holding them gets its derived layer cleared once and rewritten
MATCH (d:Decision)
WHERE d.origin = 'extracted' AND d.product_id IS NULL
RETURN count(d) AS n
LIMIT 1
