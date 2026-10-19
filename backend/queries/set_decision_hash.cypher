// set_decision_hash.cypher — the hash of the rows that wrote a decision, stamped last, so an interrupted write is redone by the next fold (D101)
UNWIND $rows AS r
MATCH (n:Decision {id: r.id})
SET n.row_hash = r.hash
