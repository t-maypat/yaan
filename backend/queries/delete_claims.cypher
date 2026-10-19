// delete_claims.cypher — claims the fold no longer derives (their events were retracted or re-resolved), removed with their edges (D101)
UNWIND $rows AS r
MATCH (n:Claim {id: r.id})
DETACH DELETE n
