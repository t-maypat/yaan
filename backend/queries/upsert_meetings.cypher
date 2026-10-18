// upsert_meetings.cypher — meeting transcript artifacts
UNWIND $rows AS r
MERGE (a:Artifact:Meeting {id: r.id})
ON CREATE SET a.observed_ms = r.observed_ms
SET a.kind = r.kind, a.title = r.title, a.text = r.text, a.url = r.url,
    a.ts_ms = r.ts_ms, a.ts_iso = r.ts_iso, a.product_id = r.product_id,
    a.external = r.external, a.raw_ref = r.raw_ref,
    a.document_type = r.document_type
