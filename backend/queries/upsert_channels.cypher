// upsert_channels.cypher — Slack channel nodes with their stage (planning / dev / support)
UNWIND $rows AS r
MERGE (c:Channel {id: r.id})
SET c.name = r.name, c.original_name = r.original_name, c.stage = r.stage,
    c.product_id = r.product_id
