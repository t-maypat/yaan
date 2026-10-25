// product_events.cypher — one product's events as Yaan knows them now (not retracted), or as it knew them at $known_at_ms (observed by then, not yet retracted): the fold's input (D101), as one JSON string per event: decoded about 5x faster (A23, D129)
MATCH (e:Event {product_id: $product_id})
WHERE ($known_at_ms IS NULL AND e.retracted_ms IS NULL)
   OR ($known_at_ms IS NOT NULL AND e.observed_ms <= $known_at_ms AND (e.retracted_ms IS NULL OR e.retracted_ms > $known_at_ms))
RETURN toJSON(properties(e)) AS event
LIMIT $limit
