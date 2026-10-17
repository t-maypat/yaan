// set_product_truck.cypher — a product's truck factor over its areas (10.3, D059): TF, the people removed in order, how many areas have an author, and how many lose all of them
UNWIND $rows AS r
MATCH (p:Product {id: r.product_id})
SET p.truck_factor = r.truck_factor, p.truck_people = r.truck_people,
    p.areas_with_author = r.areas_with_author, p.areas_orphaned = r.areas_orphaned
