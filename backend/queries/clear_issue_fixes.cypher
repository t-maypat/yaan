// clear_issue_fixes.cypher — the FIXES edges into a changed issue (the fold owns them, though they start at the PR), removed before the fold links them again (D101)
UNWIND $rows AS r
MATCH (i:Issue {id: r.id})
WITH i
MATCH (:PR)-[x:FIXES]->(i)
DELETE x
