// sentinel_changes_for.cypher — sentinel_changes for the decision chains a fold just changed (the live path, D112): the same rows, found from the topic index instead of every decision
UNWIND $topics AS t
MATCH (n:Decision {topic: t})-[:SUPERSEDES]->(o:Decision)
WHERE n.origin = 'extracted'
MATCH (n)-[:AFFECTS]->(s)
MATCH (n)-[e:EVIDENCED_BY]->(a:Artifact)
OPTIONAL MATCH (n)-[:DECIDED_BY]->(p:Person)
RETURN 'supersession' AS change, n.id AS decision_id, n.id AS trigger_id, n.kind AS kind, n.topic AS topic, o.value AS from_value, n.value AS to_value, o.valid_from_ms AS from_ms, n.valid_from_ms AS ts_ms, s.id AS subject_id, coalesce(s.title, s.name, s.id) AS subject_title, s.product_id AS product_id, a.id AS artifact_id, e.quote AS quote, p.id AS actor_id, p.name AS actor_name, p.role AS actor_role, null AS reason
LIMIT $limit
UNION ALL
UNWIND $topics AS t
MATCH (c:Claim {topic: t})-[cw:CONFLICTS_WITH]->(d:Decision)
MATCH (d)-[:AFFECTS]->(s)
OPTIONAL MATCH (c)-[:FROM]->(a:Artifact)
OPTIONAL MATCH (c)-[:ASSERTED_BY]->(p:Person)
RETURN 'conflict' AS change, d.id AS decision_id, c.id AS trigger_id, d.kind AS kind, d.topic AS topic, d.value AS from_value, c.value AS to_value, d.valid_from_ms AS from_ms, c.ts_ms AS ts_ms, s.id AS subject_id, coalesce(s.title, s.name, s.id) AS subject_title, s.product_id AS product_id, a.id AS artifact_id, c.quote AS quote, p.id AS actor_id, p.name AS actor_name, p.role AS actor_role, cw.reason AS reason
LIMIT $limit
