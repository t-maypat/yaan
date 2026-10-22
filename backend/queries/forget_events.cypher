// forget_events.cypher — close the events extracted from forgotten artifacts and blank their words: the log keeps that they were believed and when, never what they said (F2, D125)
MATCH (e:Event)
WHERE e.artifact_id IN $ids
SET e.retracted_ms = coalesce(e.retracted_ms, $now_ms), e.forgotten_ms = $now_ms,
    e.quote = '', e.issue = null, e.component = null, e.reason = null
RETURN collect(DISTINCT e.product_id) AS products, count(e) AS events
