// withdraw_alerts.cypher — open alerts whose change is gone after a rebuild (e.g. a corrected extraction) become 'withdrawn'; never deleted, and approved or dismissed ones keep their status
MATCH (a:Alert)
WHERE a.status = 'open' AND NOT a.id IN $ids
SET a.status = 'withdrawn'
RETURN count(a) AS withdrawn
LIMIT 1
