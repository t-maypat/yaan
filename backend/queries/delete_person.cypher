// delete_person.cypher — an erased person's node and every edge it had (KNOWS, COLLAB, MEMBER_OF, ...) (F2, D125)
MATCH (p:Person {id: $id})
DETACH DELETE p
