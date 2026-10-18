// upsert_prs.cypher — pull request artifacts, with current_status from their decision chain
UNWIND $rows AS r
MERGE (a:Artifact:PR {id: r.id})
ON CREATE SET a.observed_ms = r.observed_ms
SET a.kind = r.kind, a.title = r.title, a.text = r.text, a.url = r.url,
    a.ts_ms = r.ts_ms, a.ts_iso = r.ts_iso, a.product_id = r.product_id,
    a.external = r.external, a.raw_ref = r.raw_ref,
    a.repo = r.repo, a.number = r.number, a.state = r.state, a.merged = r.merged,
    a.current_status = r.current_status
