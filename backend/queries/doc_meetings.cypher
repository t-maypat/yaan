// doc_meetings.cypher — the product's meetings whose transcript names the document's type, with who spoke in each
MATCH (d:Doc {id: $doc_id})-[:IN_PRODUCT]->(prod:Product)
MATCH (prod)<-[:IN_PRODUCT]-(m:Meeting)
WHERE toLower(m.text) CONTAINS toLower(d.doc_type)
OPTIONAL MATCH (p:Person)-[s:SPOKE_IN]->(m)
WITH m, collect(DISTINCT {id: p.id, name: p.name, role: p.role, turns: s.turns}) AS speakers
ORDER BY m.ts_ms
RETURN m.id AS id, m.title AS title, m.ts_iso AS ts_iso, speakers
LIMIT 10
