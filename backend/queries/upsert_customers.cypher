// upsert_customers.cypher — customer nodes from metadata/customers_data.json
UNWIND $rows AS r
MERGE (c:Customer:Entity {id: r.id})
SET c.name = r.name, c.role = r.role
