// person_links.cypher — around one person, one row per item: areas they know (KNOWS, best first), closest collaborators (COLLAB weight), open issues assigned to them, and their latest artifacts in the product as evidence (tool 4 person_profile, R2)
MATCH (p:Person {id: $id})-[k:KNOWS]->(ar:Area)
RETURN 'knows' AS section, ar.id AS id, ar.name AS name, null AS role, ar.product_id AS product_id,
       k.author AS author, k.score AS score, k.deliveries AS n, null AS status, null AS ts_iso, null AS text
ORDER BY score DESC
LIMIT 8
UNION ALL
MATCH (p:Person {id: $id})-[c:COLLAB]->(o:Person)
RETURN 'collaborator' AS section, o.id AS id, o.name AS name, o.role AS role, null AS product_id,
       null AS author, c.w AS score,
       toInteger(coalesce(c.reviews, 0) + coalesce(c.meetings, 0) + coalesce(c.chats, 0)) AS n,
       null AS status, null AS ts_iso, null AS text
ORDER BY score DESC
LIMIT 6
UNION ALL
MATCH (i:Issue)-[a:ASSIGNED_TO]->(p:Person {id: $id})
WHERE a.to_ms IS NULL AND i.current_status IN ['reported', 'reopened']
RETURN 'assigned_open' AS section, i.id AS id, i.title AS name, null AS role, i.product_id AS product_id,
       null AS author, null AS score, i.reopen_count AS n, i.current_status AS status,
       i.reported_iso AS ts_iso, null AS text
LIMIT 20
UNION ALL
MATCH (p:Person {id: $id})-[:AUTHORED]->(x:Artifact)-[:IN_PRODUCT]->(:Product {id: $product_id})
RETURN 'recent' AS section, x.id AS id, coalesce(x.title, x.kind) AS name, null AS role,
       x.product_id AS product_id, null AS author, null AS score, x.ts_ms AS n, x.kind AS status,
       x.ts_iso AS ts_iso, left(x.text, 200) AS text
ORDER BY n DESC
LIMIT 8
