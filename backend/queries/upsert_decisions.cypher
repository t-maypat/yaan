// upsert_decisions.cypher — decision nodes (one per state in a topic's chain); an extracted one carries its product and its creating event's observed_ms, a structural one keeps when it was first seen (D101)
UNWIND $rows AS r
MERGE (d:Decision {id: r.id})
SET d.observed_ms = CASE WHEN r.origin = 'extracted' OR d.observed_ms IS NULL THEN r.observed_ms ELSE d.observed_ms END,
    d.product_id = r.product_id, d.topic = r.topic, d.kind = r.kind, d.value = r.value, d.statement = r.statement,
    d.valid_from_ms = r.valid_from_ms, d.valid_to_ms = r.valid_to_ms, d.status = r.status,
    d.confidence = r.confidence, d.origin = r.origin, d.ts_inferred = r.ts_inferred
