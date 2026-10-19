// clear_issue_edges.cypher — the edges a changed issue owns (not IN_AREA, which derive owns), removed before the fold links them again (D101)
UNWIND $rows AS r
MATCH (i:Issue {id: r.id})
WITH i
MATCH (i)-[x:IN_PRODUCT|REPORTED_BY|REPORTED_IN|ASSIGNED_TO]->()
DELETE x
