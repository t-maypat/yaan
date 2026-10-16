// product_prs.cypher — the product's own pull requests (external ones left out) with their record status chain, author, reviews, the PRs that revert them or that they revert, and what the team said to do with them in Slack (action claims with quotes)
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(pr:PR)
WHERE coalesce(pr.external, false) = false AND ($status IS NULL OR pr.current_status = $status)
  AND ($action IS NULL OR pr.current_action = $action)
OPTIONAL MATCH (au:Person)-[:AUTHORED]->(pr)
OPTIONAL MATCH (rv:Person)-[r:REVIEWED]->(pr)
WITH pr, collect(DISTINCT au.id) AS authors, collect(DISTINCT {id: rv.id, state: r.state}) AS reviews
OPTIONAL MATCH (undo:PR)-[:REVERTS]->(pr)
WITH pr, authors, reviews,
     collect(DISTINCT {number: undo.number, url: undo.url, title: undo.title, reason: undo.text}) AS reverted_by
OPTIONAL MATCH (pr)-[:REVERTS]->(orig:PR)
WITH pr, authors, reviews, reverted_by, collect(DISTINCT orig.number) AS reverts
OPTIONAL MATCH (d:Decision)-[:AFFECTS]->(pr)
WITH pr, authors, reviews, reverted_by, reverts, d
ORDER BY d.valid_from_ms
WITH pr, authors, reviews, reverted_by, reverts, collect(CASE WHEN d.kind = 'pr_status' THEN d.value END) AS chain
OPTIONAL MATCH (cl:Claim)-[:ABOUT]->(pr)
OPTIONAL MATCH (cl)-[:FROM]->(m:Artifact)
WITH pr, authors, reviews, reverted_by, reverts, chain, cl, m
ORDER BY cl.ts_ms
WITH pr, authors, reviews, reverted_by, reverts, chain,
     collect({action: cl.value, reason: cl.reason, ts_iso: cl.ts_iso, artifact_id: m.id,
              quote: cl.quote, outcome: cl.outcome}) AS said
ORDER BY pr.number
RETURN pr.id AS id, pr.number AS number, pr.title AS title, pr.url AS url,
       pr.current_status AS status, chain, authors, reviews, reverted_by, reverts,
       pr.current_action AS team_action, pr.action_reason AS action_reason, said
LIMIT $limit
