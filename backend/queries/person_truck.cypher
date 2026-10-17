// person_truck.cypher — the products whose truck factor removes this person: losing them leaves most of that product's areas with no author (tool 4 person_profile, 10.3)
MATCH (pr:Product)
WHERE $id IN coalesce(pr.truck_people, [])
RETURN pr.id AS product_id, pr.truck_factor AS truck_factor
LIMIT 50
