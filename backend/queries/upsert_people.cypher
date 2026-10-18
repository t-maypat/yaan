// upsert_people.cypher — person nodes from metadata/employee.json
UNWIND $rows AS r
MERGE (p:Person:Entity {id: r.id})
SET p.name = r.name, p.role = r.role, p.location = r.location, p.org = r.org
