// person_authored.cypher — every artifact a person wrote: what erasing them forgets (F2, D125)
MATCH (p:Person {id: $id})-[:AUTHORED]->(a:Artifact)
RETURN a.id AS id
LIMIT $limit
