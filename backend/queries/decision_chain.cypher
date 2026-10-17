// decision_chain.cypher — every state in the given decision topics: value, validity window, origin, who decided it, the evidence quotes, the claims behind it with the policy's verdict, refused claims, alerts that flagged it, the responses it caused and similar earlier cases; oldest first (tool 6 decision_lineage, R2)
UNWIND $topics AS t
MATCH (d:Decision {topic: t})
OPTIONAL MATCH (d)-[:DECIDED_BY]->(p:Person)
WITH d, collect(DISTINCT {id: p.id, name: p.name, role: p.role}) AS deciders
OPTIONAL MATCH (d)-[e:EVIDENCED_BY]->(a:Artifact)
WITH d, deciders, collect(DISTINCT {artifact_id: a.id, quote: e.quote, ts_iso: a.ts_iso}) AS evidence
OPTIONAL MATCH (c:Claim)-[:SUPPORTS]->(d)
OPTIONAL MATCH (c)-[:FROM]->(m:Artifact)
OPTIONAL MATCH (c)-[:ASSERTED_BY]->(sp:Person)
WITH d, deciders, evidence,
     collect(DISTINCT {by: sp.id, by_name: sp.name, by_role: sp.role, artifact_id: m.id, quote: c.quote,
                       ts_iso: c.ts_iso, outcome: c.outcome, authority: c.authority,
                       authority_reason: c.authority_reason}) AS claims
OPTIONAL MATCH (r:Claim)-[cw:CONFLICTS_WITH]->(d)
OPTIONAL MATCH (r)-[:FROM]->(rm:Artifact)
OPTIONAL MATCH (r)-[:ASSERTED_BY]->(rp:Person)
WITH d, deciders, evidence, claims,
     collect(DISTINCT {by: rp.id, by_name: rp.name, by_role: rp.role, artifact_id: rm.id, quote: r.quote,
                       wanted: r.value, ts_iso: r.ts_iso, reason: cw.reason}) AS refused
OPTIONAL MATCH (al:Alert)-[:FLAGS]->(d)
WITH d, deciders, evidence, claims, refused,
     collect(DISTINCT {id: al.id, kind: al.kind, status: al.status, summary: al.summary}) AS alerts
OPTIONAL MATCH (d)-[:CAUSED]->(resp:Decision)
WITH d, deciders, evidence, claims, refused, alerts,
     collect(DISTINCT {id: resp.id, value: resp.value, statement: resp.statement, origin: resp.origin}) AS responses
OPTIONAL MATCH (pre:Decision)-[pf:PRECEDENT_FOR]->(d)
OPTIONAL MATCH (pre)-[:AFFECTS]->(ps)
WITH d, deciders, evidence, claims, refused, alerts, responses,
     collect(DISTINCT {id: pre.id, value: pre.value, subject: coalesce(ps.title, ps.name, ps.id),
                       similarity: pf.similarity}) AS precedents
ORDER BY d.valid_from_ms
RETURN d.id AS id, d.topic AS topic, d.kind AS kind, d.value AS value, d.statement AS statement,
       d.valid_from_ms AS valid_from_ms, d.valid_to_ms AS valid_to_ms, d.status AS status,
       d.origin AS origin, d.confidence AS confidence, d.ts_inferred AS ts_inferred,
       deciders, evidence, claims, refused, alerts, responses, precedents
LIMIT $limit
