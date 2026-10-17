// product_truck.cypher — a product's truck factor and the people it removes, for who_knows' key-person flag (10.3, 10.7)
MATCH (p:Product {id: $product_id})
RETURN p.truck_factor AS truck_factor, p.truck_people AS truck_people,
       p.areas_with_author AS areas_with_author, p.areas_orphaned AS areas_orphaned
LIMIT 1
