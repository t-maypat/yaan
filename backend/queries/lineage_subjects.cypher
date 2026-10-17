// lineage_subjects.cypher — the product's issues and own pull requests, the subjects a decision chain can be about: decision_lineage and impact_of match a name, title or PR number against them (R2)
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(i:Issue)
RETURN i.id AS id, 'issue' AS kind, i.title AS title, null AS number, i.current_status AS status,
       i.component AS component, null AS url
LIMIT $limit
UNION ALL
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(pr:PR)
WHERE coalesce(pr.external, false) = false
RETURN pr.id AS id, 'pr' AS kind, pr.title AS title, pr.number AS number, pr.current_status AS status,
       null AS component, pr.url AS url
LIMIT $limit
