// find_docs.cypher — a product's documents matching an id, a type ("Market Research Report") or words of the title, with their authors
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(d:Doc)
WHERE d.id = $doc OR toLower(d.doc_type) = toLower($doc) OR toLower(d.title) CONTAINS toLower($doc)
OPTIONAL MATCH (p:Person)-[:AUTHORED]->(d)
WITH d, collect(DISTINCT p.id) AS authors
ORDER BY d.ts_ms
RETURN d.id AS id, d.title AS title, d.doc_type AS doc_type, d.version AS version,
       d.base_id AS base_id, d.ts_iso AS ts_iso, d.url AS url, authors
LIMIT 20
