// link_reports_to.cypher — (Person)-[:REPORTS_TO]->(Person) from the hierarchy
UNWIND $rows AS r
MATCH (p:Person {id: r.person_id})
MATCH (m:Person {id: r.manager_id})
MERGE (p)-[:REPORTS_TO]->(m)
