// rank_companies.cypher — the companies (or customers) whose customers reported a product's issues, with counts per current status and each issue's title, status and report message as evidence; the agent's ranking by issues reported / unresolved / resolved / reopened / dismissed (tool 7 rank, R2)
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(i:Issue)-[:REPORTED_BY]->(c:Customer)
OPTIONAL MATCH (c)-[:WORKS_AT]->(co:Company)
OPTIONAL MATCH (i)-[:REPORTED_IN]->(m:Artifact)
WITH CASE WHEN $entity = 'customer' OR co IS NULL THEN c.id ELSE co.id END AS key,
     CASE WHEN $entity = 'customer' OR co IS NULL THEN c.name ELSE co.name END AS name, i, m
WITH key, name, i, collect(m.id)[0] AS report_id
WITH key, name, collect({id: i.id, title: i.title, status: i.current_status, reopen_count: i.reopen_count,
                         report_id: report_id}) AS issues
RETURN key AS id, name, issues
LIMIT $limit
