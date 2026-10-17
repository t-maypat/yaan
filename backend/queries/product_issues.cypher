// product_issues.cypher — the product's canonical customer issues with current status, status history (claims with quotes, and the policy's reason when a claim was refused), assignee, reporting customers and fixing PRs (slice B, D049, D057)
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(i:Issue)
WHERE $status IS NULL OR i.current_status = $status
OPTIONAL MATCH (i)-[:REPORTED_BY]->(c:Customer)
OPTIONAL MATCH (c)-[:WORKS_AT]->(co:Company)
WITH i, collect(DISTINCT {id: c.id, name: c.name, company: co.name}) AS customers
OPTIONAL MATCH (i)-[:ASSIGNED_TO]->(a:Person)
WITH i, customers, collect(DISTINCT {id: a.id, name: a.name, role: a.role}) AS assignees
OPTIONAL MATCH (pr:PR)-[:FIXES]->(i)
OPTIONAL MATCH (pa:Person)-[:AUTHORED]->(pr)
WITH i, customers, assignees,
     collect(DISTINCT {id: pr.id, number: pr.number, url: pr.url, author: pa.id}) AS fixed_by
OPTIONAL MATCH (cl:Claim)-[:ABOUT]->(i)
OPTIONAL MATCH (cl)-[:FROM]->(m:Artifact)
OPTIONAL MATCH (cl)-[:ASSERTED_BY]->(sp:Person)
OPTIONAL MATCH (cl)-[cw:CONFLICTS_WITH]->(:Decision)
WITH i, customers, assignees, fixed_by, cl, m, sp, cw
ORDER BY cl.ts_ms
WITH i, customers, assignees, fixed_by,
     collect({status: cl.value, ts_iso: cl.ts_iso, ts_ms: cl.ts_ms, by: sp.id, by_name: sp.name, by_role: sp.role, artifact_id: m.id,
              quote: cl.quote, outcome: cl.outcome, refused: cw.reason}) AS history
ORDER BY i.reported_ms
RETURN i.id AS id, i.title AS title, i.component AS component, i.current_status AS status,
       i.reopen_count AS reopen_count, customers, assignees, fixed_by, history
LIMIT $limit
