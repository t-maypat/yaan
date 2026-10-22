// derived_state.cypher — a product's derived nodes now in the graph with the hash of the rows that wrote them and the topic they belong to: the fold writes only what changed, removes only what went stale, and tells the Sentinel which topics moved (D101, D112); one JSON row each, [id, label, hash, topic] (A23, D129)
MATCH (c:Claim {product_id: $product_id})
RETURN toJSON([c.id, 'Claim', c.row_hash, c.topic]) AS row
LIMIT $limit
UNION ALL
MATCH (i:Issue {product_id: $product_id})
RETURN toJSON([i.id, 'Issue', i.row_hash, i.id + '/status']) AS row
LIMIT $limit
UNION ALL
MATCH (d:Decision {product_id: $product_id})
WHERE d.origin = 'extracted'
RETURN toJSON([d.id, 'Decision', d.row_hash, d.topic]) AS row
LIMIT $limit
