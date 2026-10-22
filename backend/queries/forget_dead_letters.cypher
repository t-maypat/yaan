// forget_dead_letters.cypher — dead letters keep a whole item for a retry: delete those of forgotten artifacts (F2, D125)
MATCH (d:DeadLetter)
WHERE d.artifact_id IN $ids
DETACH DELETE d
