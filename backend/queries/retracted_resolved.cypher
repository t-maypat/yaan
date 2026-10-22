// retracted_resolved.cypher — a product's retracted events that still carry the last pass's resolution: what they made is gone unless another event makes it, and their results are cleared after the next pass (D129)
MATCH (e:Event {product_id: $product_id})
WHERE e.retracted_ms IS NOT NULL AND e.res_v IS NOT NULL
RETURN toJSON(properties(e)) AS event
LIMIT $limit
