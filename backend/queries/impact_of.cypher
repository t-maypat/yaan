// impact_of.cypher — blast radius of one decision (tool 11, §9.5, D040): for its issue, the fixing PRs with their authors and reviewers, the reporting customers and their companies, assignees, other issues on the same component; for its PR, the author, reviewers and the issues it fixes; the evidence and decider of the state it replaced; WORKS_ON teams; CAUSED decisions ≤ 3 hops. One row per item: kind, id, name, role, why, via (the id it hangs off), detail, quote, artifact_id, ts_iso
MATCH (:Decision {id: $decision_id})-[:AFFECTS]->(i:Issue)<-[:FIXES]-(pr:PR)
RETURN 'pr' AS kind, pr.id AS id, coalesce(pr.title, pr.id) AS name, null AS role, 'fixes_issue' AS why, i.id AS via, pr.current_status AS detail, null AS quote, pr.id AS artifact_id, pr.ts_iso AS ts_iso
LIMIT $limit
UNION ALL
MATCH (:Decision {id: $decision_id})-[:AFFECTS]->(:Issue)<-[:FIXES]-(pr:PR)<-[:AUTHORED]-(p:Person)
RETURN DISTINCT 'person' AS kind, p.id AS id, p.name AS name, p.role AS role, 'authored_fix' AS why, pr.id AS via, null AS detail, null AS quote, pr.id AS artifact_id, pr.ts_iso AS ts_iso
LIMIT $limit
UNION ALL
MATCH (:Decision {id: $decision_id})-[:AFFECTS]->(:Issue)<-[:FIXES]-(pr:PR)<-[rv:REVIEWED]-(p:Person)
WITH p, pr, rv ORDER BY rv.ts_ms
WITH p, pr, collect(rv.state) AS states, collect(rv.comment) AS comments
RETURN 'person' AS kind, p.id AS id, p.name AS name, p.role AS role, 'reviewed_fix' AS why, pr.id AS via, states[size(states) - 1] AS detail, comments[size(comments) - 1] AS quote, pr.id AS artifact_id, null AS ts_iso
LIMIT $limit
UNION ALL
MATCH (:Decision {id: $decision_id})-[:AFFECTS]->(i:Issue)-[:REPORTED_BY]->(c:Customer)
RETURN 'customer' AS kind, c.id AS id, c.name AS name, c.role AS role, 'reported_issue' AS why, i.id AS via, null AS detail, null AS quote, null AS artifact_id, null AS ts_iso
LIMIT $limit
UNION ALL
MATCH (:Decision {id: $decision_id})-[:AFFECTS]->(:Issue)-[:REPORTED_BY]->(c:Customer)-[:WORKS_AT]->(co:Company)
RETURN DISTINCT 'company' AS kind, co.id AS id, co.name AS name, null AS role, 'reporter_works_at' AS why, c.id AS via, null AS detail, null AS quote, null AS artifact_id, null AS ts_iso
LIMIT $limit
UNION ALL
MATCH (:Decision {id: $decision_id})-[:AFFECTS]->(i:Issue)-[a:ASSIGNED_TO]->(p:Person)
RETURN 'person' AS kind, p.id AS id, p.name AS name, p.role AS role, 'assigned' AS why, i.id AS via, CASE WHEN a.to_ms IS NULL THEN 'current' ELSE 'past' END AS detail, null AS quote, null AS artifact_id, null AS ts_iso
LIMIT $limit
UNION ALL
MATCH (:Decision {id: $decision_id})-[:AFFECTS]->(i:Issue)-[:IN_PRODUCT]->(:Product)<-[:IN_PRODUCT]-(j:Issue)
WHERE j.component = i.component AND j.id <> i.id
RETURN 'issue' AS kind, j.id AS id, j.title AS name, null AS role, 'same_component' AS why, i.id AS via, j.current_status AS detail, null AS quote, null AS artifact_id, j.reported_iso AS ts_iso
LIMIT $limit
UNION ALL
MATCH (:Decision {id: $decision_id})-[:AFFECTS]->(pr:PR)<-[:AUTHORED]-(p:Person)
RETURN 'person' AS kind, p.id AS id, p.name AS name, p.role AS role, 'authored_pr' AS why, pr.id AS via, null AS detail, null AS quote, pr.id AS artifact_id, pr.ts_iso AS ts_iso
LIMIT $limit
UNION ALL
MATCH (:Decision {id: $decision_id})-[:AFFECTS]->(pr:PR)<-[rv:REVIEWED]-(p:Person)
WITH p, pr, rv ORDER BY rv.ts_ms
WITH p, pr, collect(rv.state) AS states, collect(rv.comment) AS comments
RETURN 'person' AS kind, p.id AS id, p.name AS name, p.role AS role, 'reviewed_pr' AS why, pr.id AS via, states[size(states) - 1] AS detail, comments[size(comments) - 1] AS quote, pr.id AS artifact_id, null AS ts_iso
LIMIT $limit
UNION ALL
MATCH (:Decision {id: $decision_id})-[:AFFECTS]->(pr:PR)-[:FIXES]->(j:Issue)
RETURN 'issue' AS kind, j.id AS id, j.title AS name, null AS role, 'fixed_by_pr' AS why, pr.id AS via, j.current_status AS detail, null AS quote, null AS artifact_id, j.reported_iso AS ts_iso
LIMIT $limit
UNION ALL
MATCH (:Decision {id: $decision_id})-[:SUPERSEDES]->(o:Decision)-[e:EVIDENCED_BY]->(a:Artifact)
RETURN 'artifact' AS kind, a.id AS id, coalesce(a.title, a.id) AS name, null AS role, 'evidenced_old_state' AS why, o.id AS via, o.value AS detail, e.quote AS quote, a.id AS artifact_id, a.ts_iso AS ts_iso
LIMIT $limit
UNION ALL
MATCH (:Decision {id: $decision_id})-[:SUPERSEDES]->(o:Decision)-[:DECIDED_BY]->(p:Person)
RETURN 'person' AS kind, p.id AS id, p.name AS name, p.role AS role, 'decided_old_state' AS why, o.id AS via, o.value AS detail, null AS quote, null AS artifact_id, null AS ts_iso
LIMIT $limit
UNION ALL
MATCH (n:Decision {id: $decision_id})-[e:EVIDENCED_BY]->(a:Artifact)
RETURN 'artifact' AS kind, a.id AS id, coalesce(a.title, a.id) AS name, null AS role, 'evidenced_this_state' AS why, n.id AS via, n.value AS detail, e.quote AS quote, a.id AS artifact_id, a.ts_iso AS ts_iso
LIMIT $limit
UNION ALL
MATCH (:Decision {id: $decision_id})-[:AFFECTS]->()-[:IN_PRODUCT]->(:Product)<-[w:WORKS_ON]-(t:Team)
WITH t, collect(w.stage) AS stages, sum(w.artifacts) AS n
RETURN 'team' AS kind, t.id AS id, t.name AS name, null AS role, 'works_on_product' AS why, null AS via, toString(toInteger(n)) AS detail, null AS quote, null AS artifact_id, null AS ts_iso
LIMIT $limit
UNION ALL
MATCH (:Decision {id: $decision_id})-[:CAUSED*1..3]->(x:Decision)
RETURN DISTINCT 'decision' AS kind, x.id AS id, x.statement AS name, null AS role, 'caused' AS why, null AS via, x.value AS detail, null AS quote, null AS artifact_id, null AS ts_iso
LIMIT $limit
