// states_at.cypher — the decisions in force at one moment for a product's issues and own PRs (valid_from_ms <= t < valid_to_ms, or still open): the as-of view of list_issues, list_prs and decision_lineage (R2)
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(i:Issue)<-[:AFFECTS]-(d:Decision)
WHERE d.kind = 'issue_status' AND d.valid_from_ms <= $as_of_ms
  AND (d.valid_to_ms IS NULL OR d.valid_to_ms > $as_of_ms)
RETURN i.id AS subject_id, d.kind AS kind, d.value AS value, d.id AS decision_id
LIMIT $limit
UNION ALL
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(pr:PR)<-[:AFFECTS]-(d:Decision)
WHERE coalesce(pr.external, false) = false AND d.kind IN ['pr_status', 'pr_action']
  AND d.valid_from_ms <= $as_of_ms AND (d.valid_to_ms IS NULL OR d.valid_to_ms > $as_of_ms)
RETURN pr.id AS subject_id, d.kind AS kind, d.value AS value, d.id AS decision_id
LIMIT $limit
