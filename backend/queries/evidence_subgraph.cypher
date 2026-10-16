// evidence_subgraph.cypher — the evidence around cited artifacts for the UI: authors, product, customers mentioned, the claims quoting them, what those claims are about (issue -> customer -> company) and the decisions they support
UNWIND $ids AS id
MATCH (a:Artifact {id: id})<-[:AUTHORED]-(p:Person)
RETURN p.id AS src_id, 'person' AS src_label, p.name AS src_name, 'AUTHORED' AS rel, a.id AS dst_id, a.kind AS dst_label, coalesce(a.title, a.id) AS dst_name
LIMIT $limit
UNION ALL
UNWIND $ids AS id
MATCH (a:Artifact {id: id})-[:IN_PRODUCT]->(x:Product)
RETURN a.id AS src_id, a.kind AS src_label, coalesce(a.title, a.id) AS src_name, 'IN_PRODUCT' AS rel, x.id AS dst_id, 'product' AS dst_label, x.name AS dst_name
LIMIT $limit
UNION ALL
UNWIND $ids AS id
MATCH (a:Artifact {id: id})-[:MENTIONS]->(x:Customer)
RETURN a.id AS src_id, a.kind AS src_label, coalesce(a.title, a.id) AS src_name, 'MENTIONS' AS rel, x.id AS dst_id, 'customer' AS dst_label, x.name AS dst_name
LIMIT $limit
UNION ALL
UNWIND $ids AS id
MATCH (a:Artifact {id: id})<-[:FROM]-(c:Claim)
RETURN c.id AS src_id, 'claim' AS src_label, c.value AS src_name, 'FROM' AS rel, a.id AS dst_id, a.kind AS dst_label, coalesce(a.title, a.id) AS dst_name
LIMIT $limit
UNION ALL
UNWIND $ids AS id
MATCH (a:Artifact {id: id})<-[:FROM]-(c:Claim)-[:ABOUT]->(x)
RETURN c.id AS src_id, 'claim' AS src_label, c.value AS src_name, 'ABOUT' AS rel, x.id AS dst_id, coalesce(x.kind, 'issue') AS dst_label, coalesce(x.title, x.name, x.id) AS dst_name
LIMIT $limit
UNION ALL
UNWIND $ids AS id
MATCH (a:Artifact {id: id})<-[:FROM]-(:Claim)-[:ABOUT]->(i:Issue)-[:REPORTED_BY]->(cu:Customer)
RETURN i.id AS src_id, 'issue' AS src_label, i.title AS src_name, 'REPORTED_BY' AS rel, cu.id AS dst_id, 'customer' AS dst_label, cu.name AS dst_name
LIMIT $limit
UNION ALL
UNWIND $ids AS id
MATCH (a:Artifact {id: id})-[:MENTIONS]->(cu:Customer)-[:WORKS_AT]->(co:Company)
RETURN cu.id AS src_id, 'customer' AS src_label, cu.name AS src_name, 'WORKS_AT' AS rel, co.id AS dst_id, 'company' AS dst_label, co.name AS dst_name
LIMIT $limit
UNION ALL
UNWIND $ids AS id
MATCH (a:Artifact {id: id})<-[:FROM]-(:Claim)-[:ABOUT]->(:Issue)-[:REPORTED_BY]->(cu:Customer)-[:WORKS_AT]->(co:Company)
RETURN cu.id AS src_id, 'customer' AS src_label, cu.name AS src_name, 'WORKS_AT' AS rel, co.id AS dst_id, 'company' AS dst_label, co.name AS dst_name
LIMIT $limit
UNION ALL
UNWIND $ids AS id
MATCH (a:Artifact {id: id})<-[:FROM]-(c:Claim)-[:SUPPORTS]->(d:Decision)
RETURN c.id AS src_id, 'claim' AS src_label, c.value AS src_name, 'SUPPORTS' AS rel, d.id AS dst_id, 'decision' AS dst_label, d.statement AS dst_name
LIMIT $limit
