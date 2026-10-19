// upsert_people.cypher — person nodes from metadata/employee.json or people.yaml, with an explicit authority when people.yaml gives one (D102)
UNWIND $rows AS r
MERGE (p:Person:Entity {id: r.id})
SET p.name = r.name, p.role = r.role, p.location = r.location, p.org = r.org,
    p.authority = r.authority
