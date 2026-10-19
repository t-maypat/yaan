// delete_issues.cypher — issues the fold no longer derives (their events were retracted or re-resolved), removed with their edges (D101)
UNWIND $rows AS r
MATCH (n:Issue {id: r.id})
DETACH DELETE n
