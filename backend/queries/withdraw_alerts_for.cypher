// withdraw_alerts_for.cypher — withdraw_alerts for the topics a fold just changed (D112): an open alert on one of them whose change is gone becomes 'withdrawn'
MATCH (a:Alert)
WHERE a.status = 'open' AND a.topic IN $topics AND NOT a.id IN $ids
SET a.status = 'withdrawn'
RETURN count(a) AS withdrawn
LIMIT 1
