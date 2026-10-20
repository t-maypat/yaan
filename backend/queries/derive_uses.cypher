// derive_uses.cypher — (Company)-[:USES {issues, customers, first_ms, last_ms}]->(Product): a company uses a product when its customers report issues in it (S9, 10.6, D066)
MATCH (p:Product)<-[:IN_PRODUCT]-(i:Issue)-[:REPORTED_BY]->(c:Customer)-[:WORKS_AT]->(co:Company)
WITH co, p, count(DISTINCT i) AS issues, count(DISTINCT c) AS customers, min(i.reported_ms) AS first_ms,
     max(i.reported_ms) AS last_ms
MERGE (co)-[u:USES]->(p)
SET u.issues = issues, u.customers = customers, u.first_ms = first_ms, u.last_ms = last_ms,
    u.run = $run
