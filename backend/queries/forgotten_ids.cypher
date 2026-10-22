// forgotten_ids.cypher — which of these item ids were forgotten (have a Tombstone): a connector must not bring them back (F2, D125)
MATCH (t:Tombstone)
WHERE t.id IN $ids
RETURN t.id AS id
LIMIT $limit
