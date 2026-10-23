// derived_state_topics.cypher — derived_state for some topics only: the nodes of the topics whose inputs changed, with their row hash (22.7, D142); one JSON row each, [id, label, hash, topic] (A23)
MATCH (c:Claim)
WHERE c.topic IN $topics AND c.product_id = $product_id
RETURN toJSON([c.id, 'Claim', c.row_hash, c.topic]) AS row
LIMIT $limit
UNION ALL
MATCH (i:Issue)
WHERE i.id IN $issue_ids AND i.product_id = $product_id
RETURN toJSON([i.id, 'Issue', i.row_hash, i.id + '/status']) AS row
LIMIT $limit
UNION ALL
MATCH (d:Decision)
WHERE d.topic IN $topics AND d.product_id = $product_id AND d.origin = 'extracted'
RETURN toJSON([d.id, 'Decision', d.row_hash, d.topic]) AS row
LIMIT $limit
