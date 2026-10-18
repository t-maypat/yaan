// link_works_at.cypher — (Customer)-[:WORKS_AT]->(Company)
UNWIND $rows AS r
MATCH (c:Customer {id: r.customer_id})
MATCH (co:Company {id: r.company_id})
MERGE (c)-[:WORKS_AT]->(co)
