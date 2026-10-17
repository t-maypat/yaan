// person_radius.cypher — what depends on one person (11.5, D062): the areas they are the only author of, the people who also know those areas (backups) and their manager; one row per item in impact_of's shape
MATCH (pe:Person {id: $person_id})-[k:KNOWS]->(a:Area)
WHERE k.author = true AND size(a.authors) = 1
RETURN 'area' AS kind, a.id AS id, a.name AS name, null AS role, 'only_expert' AS why, pe.id AS via, a.product_id AS detail, null AS quote, null AS artifact_id, null AS ts_iso
LIMIT $limit
UNION ALL
MATCH (pe:Person {id: $person_id})-[k:KNOWS]->(a:Area)<-[k2:KNOWS]-(b:Person)
WHERE k.author = true AND size(a.authors) = 1 AND b.id <> pe.id
RETURN 'person' AS kind, b.id AS id, b.name AS name, b.role AS role, 'could_back_up' AS why, a.id AS via, toString(k2.score) AS detail, null AS quote, null AS artifact_id, null AS ts_iso
LIMIT $limit
UNION ALL
MATCH (pe:Person {id: $person_id})-[:REPORTS_TO]->(m:Person)
RETURN 'person' AS kind, m.id AS id, m.name AS name, m.role AS role, 'manager' AS why, pe.id AS via, null AS detail, null AS quote, null AS artifact_id, null AS ts_iso
LIMIT $limit
