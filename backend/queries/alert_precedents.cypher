// alert_precedents.cypher — earlier responses (approved or dismissed) to alerts of the same kind in the same product, newest first: the PRECEDENT_FOR sources of a new response (11.4, §9.5)
MATCH (a:Alert)
WHERE a.kind = $kind AND a.product_id = $product_id AND a.response_id IS NOT NULL AND a.id <> $alert_id
WITH a
MATCH (r:Decision {id: a.response_id})
RETURN r.id AS id, r.value AS value, r.statement AS statement, a.id AS alert_id, r.valid_from_ms AS ts_ms
ORDER BY r.valid_from_ms DESC
LIMIT $limit
