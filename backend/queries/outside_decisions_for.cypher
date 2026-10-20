// outside_decisions_for.cypher — outside_decisions for the decision chains a fold just changed (the live path, D112): a dismissal decided outside the area's working group, found from the topic index
UNWIND $topics AS t
MATCH (d:Decision {topic: t, status: 'active', origin: 'extracted', value: 'dismissed'})-[:AFFECTS]->(i:Issue)-[:IN_AREA]->(a:Area)
MATCH (d)-[:DECIDED_BY]->(p:Person)-[:IN_COMMUNITY]->(pc:Community)
MATCH (d)-[e:EVIDENCED_BY]->(m:Artifact)
OPTIONAL MATCH (k:Person)-[kn:KNOWS]->(a)
WHERE kn.deliveries > 0
OPTIONAL MATCH (k)-[:IN_COMMUNITY]->(kc:Community)
WITH d, i, a, p, pc, m, e, collect(DISTINCT kc.id) AS groups, collect(DISTINCT k.name) AS workers
WHERE size(groups) > 0 AND NOT pc.id IN groups
RETURN 'outside_group' AS change, d.id AS decision_id, 'outside:' + d.id AS trigger_id, d.kind AS kind, d.topic AS topic,
       null AS from_value, d.value AS to_value, null AS from_ms, d.valid_from_ms AS ts_ms, i.id AS subject_id,
       i.title AS subject_title, i.product_id AS product_id, m.id AS artifact_id, e.quote AS quote,
       p.id AS actor_id, p.name AS actor_name, p.role AS actor_role,
       a.name + ' work was done by ' + reduce(s = '', w IN workers | s + CASE WHEN s = '' THEN '' ELSE ', ' END + w) + '; the decider is in ' + pc.label AS reason
LIMIT $limit
