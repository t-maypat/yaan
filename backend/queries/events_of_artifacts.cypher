// events_of_artifacts.cypher — the resolved events of artifacts about to be forgotten, read before their words are blanked: the refold needs them once, in memory, to know which issues they made (D129)
MATCH (e:Event)
WHERE e.artifact_id IN $ids AND e.res_v IS NOT NULL
RETURN toJSON(properties(e)) AS event
LIMIT $limit
