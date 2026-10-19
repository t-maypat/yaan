// derived_state.cypher — a product's derived nodes now in the graph with the hash of the rows that wrote them: the fold writes only what changed and removes only what went stale (D101)
MATCH (c:Claim {product_id: $product_id})
RETURN c.id AS id, 'Claim' AS label, c.row_hash AS hash
LIMIT $limit
UNION ALL
MATCH (i:Issue {product_id: $product_id})
RETURN i.id AS id, 'Issue' AS label, i.row_hash AS hash
LIMIT $limit
UNION ALL
MATCH (d:Decision {product_id: $product_id})
WHERE d.origin = 'extracted'
RETURN d.id AS id, 'Decision' AS label, d.row_hash AS hash
LIMIT $limit
