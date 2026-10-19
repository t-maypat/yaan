// clear_decision_edges.cypher — the edges a changed extracted decision owns (not CAUSED or PRECEDENT_FOR, which the Sentinel and derive own), removed before the fold links them again (D101)
UNWIND $rows AS r
MATCH (d:Decision {id: r.id})
WITH d
MATCH (d)-[x:AFFECTS|DECIDED_BY|EVIDENCED_BY|SUPERSEDES]->()
DELETE x
