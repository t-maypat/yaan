// upsert_companies.cypher — company nodes from the customers' `company` field
UNWIND $rows AS r
MERGE (c:Company:Entity {id: r.id})
SET c.name = r.name
