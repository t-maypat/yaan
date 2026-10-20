// delete_dead_letters.cypher — drop dead letters whose item has now been ingested (H1, D107)
UNWIND $rows AS r
MATCH (d:DeadLetter {id: r.id})
DELETE d
RETURN count(*) AS n
