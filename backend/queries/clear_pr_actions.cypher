// clear_pr_actions.cypher — a product's PRs whose stated action the fold no longer derives lose it (D101)
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(pr:PR)
WHERE pr.current_action IS NOT NULL AND NOT pr.id IN $keep
SET pr.current_action = null, pr.action_reason = null, pr.action_since_ms = null
RETURN count(pr) AS cleared
