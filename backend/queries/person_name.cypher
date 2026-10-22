// person_name.cypher — one person's display name by id (erasure looks it up before deleting the node, to find plain-text mentions) (F2, D125)
MATCH (p:Person {id: $id})
RETURN p.name AS name
LIMIT 1
