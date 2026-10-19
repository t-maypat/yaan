// release_lock.cypher — give a graph's writer lock back, only if this holder still has it (D101)
MATCH (l:Lock {id: $name})
WHERE l.holder = $holder
SET l.holder = null, l.until_ms = null
RETURN count(l) AS released
