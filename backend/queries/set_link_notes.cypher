// set_link_notes.cypher — per artifact: mentions found by linking and the unresolved ones (S4 -> S10)
UNWIND $rows AS r
MATCH (a:Artifact {id: r.artifact_id})
SET a.mention_count = r.mentions, a.unresolved = r.unresolved
