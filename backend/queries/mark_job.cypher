// mark_job.cypher — a live write made a periodic job due (dirty_ms), or the job ran (done_ms) (D112)
MERGE (j:Job {id: $id})
SET j.dirty_ms = coalesce($dirty_ms, j.dirty_ms), j.done_ms = coalesce($done_ms, j.done_ms)
RETURN j.id AS id
