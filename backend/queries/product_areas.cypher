// product_areas.cypher — a product's areas (components and work items) with their names, aliases and authors: who_knows matches a topic against them (10.7)
MATCH (a:Area)-[:IN_PRODUCT]->(:Product {id: $product_id})
RETURN a.id AS id, a.name AS name, a.kind AS kind, a.aliases AS aliases, a.authors AS authors,
       a.deliveries AS deliveries
LIMIT $limit
