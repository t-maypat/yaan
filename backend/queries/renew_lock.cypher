// renew_lock.cypher — extend a graph's writer lock, only while this holder still has it: a long write keeps its lease, a lost one stays lost (H2, D110)
MATCH (l:Lock {id: $name})
WHERE l.holder = $holder
SET l.until_ms = $now_ms + $ttl_ms
RETURN l.holder AS holder
LIMIT 1
