// directory_entries.cypher — customers and people by id, with a customer's company and a person's role: the "metadata as documents" baseline's directory (D103), the joins the graph makes, handed to plain RAG as text
UNWIND $ids AS id
OPTIONAL MATCH (c:Customer {id: id})
OPTIONAL MATCH (c)-[:WORKS_AT]->(co:Company)
OPTIONAL MATCH (p:Person {id: id})
WITH id, c, co, p
WHERE c IS NOT NULL OR p IS NOT NULL
RETURN id, coalesce(c.name, p.name) AS name, co.name AS company, p.role AS role
LIMIT $limit
