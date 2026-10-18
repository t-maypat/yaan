// issue_reports.cypher — customer issue reports: support-channel messages in the product that mention a customer, with each customer's company and the people mentioned
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(m:Message)-[:IN_CHANNEL]->(ch:Channel)
WHERE ch.stage = 'support'
MATCH (m)-[:MENTIONS]->(c:Customer)
OPTIONAL MATCH (c)-[:WORKS_AT]->(co:Company)
OPTIONAL MATCH (m)-[:MENTIONS]->(p:Person)
OPTIONAL MATCH (au:Person)-[:AUTHORED]->(m)
WITH m, ch, au, collect(DISTINCT {id: c.id, name: c.name, company: co.name}) AS customers,
     collect(DISTINCT p.id) AS mentioned
ORDER BY m.ts_ms
RETURN m.id AS id, m.ts_iso AS ts_iso, ch.name AS channel, m.text AS text, au.id AS author,
       customers, mentioned
LIMIT $limit
