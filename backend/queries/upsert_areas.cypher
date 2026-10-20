// upsert_areas.cypher — Area nodes (10.0, D059): a product's bug-report components and development work items, the units of expertise and of the truck factor
UNWIND $rows AS r
MERGE (a:Area:Entity {id: r.id})
SET a.name = r.name, a.kind = r.kind, a.product_id = r.product_id, a.aliases = r.aliases,
    a.deliveries = r.deliveries, a.authors = r.authors, a.first_ms = r.first_ms, a.last_ms = r.last_ms,
    a.run = r.run
WITH a, r
MATCH (p:Product {id: r.product_id})
MERGE (a)-[:IN_PRODUCT]->(p)
