// upsert_products.cypher — product nodes from channel names, with codenames found in artifacts (D029)
UNWIND $rows AS r
MERGE (p:Product:Entity {id: r.id})
SET p.name = r.name, p.current_name = r.name, p.aliases = r.aliases
