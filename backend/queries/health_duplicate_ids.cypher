// health_duplicate_ids.cypher — ids held by more than one node (a label-set mistake, A16)
MATCH (n)
WHERE n.id IS NOT NULL
WITH n.id AS id, count(n) AS copies
WHERE copies > 1
RETURN id, copies
ORDER BY copies DESC
LIMIT 20
