// retract_events.cypher — a newer version of an artifact no longer states these events: close them (retracted_ms), never delete them, so the belief at an earlier moment can be replayed (D101)
MATCH (e:Event {artifact_id: $artifact_id})
WHERE e.retracted_ms IS NULL AND NOT e.id IN $keep
SET e.retracted_ms = $now_ms
RETURN count(e) AS retracted
