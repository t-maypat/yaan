// upsert_decisions.cypher — decision nodes (one per state in a topic's chain)
UNWIND $rows AS r
MERGE (d:Decision {id: r.id})
ON CREATE SET d.observed_ms = r.observed_ms
SET d.topic = r.topic, d.kind = r.kind, d.value = r.value, d.statement = r.statement,
    d.valid_from_ms = r.valid_from_ms, d.valid_to_ms = r.valid_to_ms, d.status = r.status,
    d.confidence = r.confidence, d.origin = r.origin, d.ts_inferred = r.ts_inferred
