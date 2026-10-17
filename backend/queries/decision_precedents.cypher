// decision_precedents.cypher — the earlier decisions that are precedents for the given one (similar subject, same kind of decision), most similar first, with who decided and the evidence quote (10.6, D066)
MATCH (p:Decision)-[e:PRECEDENT_FOR {basis: 'similar_subject'}]->(:Decision {id: $decision_id})
MATCH (p)-[:AFFECTS]->(s)
OPTIONAL MATCH (p)-[:DECIDED_BY]->(by:Person)
OPTIONAL MATCH (p)-[ev:EVIDENCED_BY]->(m:Artifact)
RETURN p.id AS id, p.value AS value, p.valid_from_ms AS ts_ms, s.id AS subject_id,
       coalesce(s.title, s.name, s.id) AS subject, e.similarity AS similarity, by.name AS decided_by,
       m.id AS artifact_id, ev.quote AS quote
ORDER BY similarity DESC
LIMIT $limit
