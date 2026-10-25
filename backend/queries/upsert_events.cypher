// upsert_events.cypher — verified events, the derived layer's system of record (D101): immutable; observed_ms is when Yaan first learned one, and an event asserted again after a retraction is live again
UNWIND $rows AS r
MERGE (e:Event {id: r.id})
ON CREATE SET e.observed_ms = r.observed_ms, e.artifact_id = r.artifact_id, e.product_id = r.product,
    e.channel = r.channel, e.type = r.type, e.quote = r.quote, e.issue = r.issue, e.component = r.component,
    e.customer = r.customer, e.person = r.person, e.pr = r.pr, e.reason = r.reason, e.decisive = r.decisive,
    e.confidence = r.confidence, e.speaker = r.speaker, e.ts_ms = r.ts_ms, e.ts_iso = r.ts_iso,
    e.start = r.start, e.end = r.end, e.msg = r.msg, e.unit_id = r.unit_id, e.moved = r.moved
SET e.retracted_ms = null
