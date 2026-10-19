// clear_claim_edges.cypher — the edges a changed claim owns, removed before the fold links them again (D101)
UNWIND $rows AS r
MATCH (c:Claim {id: r.id})
WITH c
MATCH (c)-[x:FROM|ASSERTED_BY|ABOUT|SUPPORTS|CONFLICTS_WITH]->()
DELETE x
