// product_clock.cypher — each product's last artifact time: "now" on HERB's clock, for recency (10.2, D057)
MATCH (a:Artifact)-[:IN_PRODUCT]->(p:Product)
RETURN p.id AS product_id, max(a.ts_ms) AS last_ms
LIMIT $limit
