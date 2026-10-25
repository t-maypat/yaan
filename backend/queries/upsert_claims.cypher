// upsert_claims.cypher — Claim nodes: one verified extracted event each, with the fold's outcome and the policy verdict (S5-S8); observed_ms is its event's, when Yaan first learned it (D101)
UNWIND $rows AS r
MERGE (c:Claim {id: r.id})
SET c.observed_ms = r.observed_ms, c.topic = r.topic, c.kind = r.kind, c.type = r.type, c.value = r.value, c.quote = r.quote,
    c.ts_ms = r.ts_ms, c.ts_iso = r.ts_iso, c.decisive = r.decisive, c.confidence = r.confidence,
    c.outcome = r.outcome, c.authority = r.authority, c.authority_rule = r.authority_rule,
    c.authority_reason = r.authority_reason, c.reason = r.reason, c.resolved_how = r.resolved_how,
    c.product_id = r.product_id
