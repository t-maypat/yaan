// delete_decisions.cypher — decisions the fold no longer derives (their events were retracted or re-resolved), removed with their edges (D101)
UNWIND $rows AS r
MATCH (n:Decision {id: r.id})
DETACH DELETE n
