// key_people.cypher — each product's first truck-factor person and the areas they are the only author of, in any product: the input for key_person alerts (11.5, D062)
MATCH (pr:Product)
WHERE size(coalesce(pr.truck_people, [])) > 0
WITH pr, pr.truck_people[0] AS pid, pr.truck_factor AS tf
MATCH (pe:Person {id: pid})-[k:KNOWS]->(a:Area)
WHERE k.author = true AND size(a.authors) = 1
RETURN pr.id AS flagged_in, tf, pe.id AS person_id, pe.name AS name, pe.role AS role,
       a.id AS area_id, a.name AS area, a.product_id AS area_product, a.last_ms AS last_ms
LIMIT $limit
