// acquire_lock.cypher — take a graph's writer lock if it is free or its holder's lease ran out: one statement, so two writers can't both get it (D101)
MERGE (l:Lock {id: $name})
WITH l
WHERE l.holder IS NULL OR l.until_ms < $now_ms
SET l.holder = $holder, l.until_ms = $now_ms + $ttl_ms
RETURN l.holder AS holder
