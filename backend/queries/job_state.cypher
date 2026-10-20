// job_state.cypher — when a periodic job last ran and when a live write last made it due (D112)
MATCH (j:Job {id: $id})
RETURN j.dirty_ms AS dirty_ms, j.done_ms AS done_ms
LIMIT 1
