// suppressed_ids.cypher — which of these hashed person ids were erased: their items stay out (F2, D125)
MATCH (s:Suppressed)
WHERE s.id IN $ids
RETURN s.id AS id
LIMIT $limit
