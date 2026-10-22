// erase_dead_letters.cypher — dead letters whose stored item names an erased person (as author or by @-mention): deleted, never retried (F2, D125)
MATCH (d:DeadLetter)
WHERE d.payload CONTAINS $id
DETACH DELETE d
