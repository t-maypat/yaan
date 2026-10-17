// area_issues.cypher — every canonical issue with its product and component, the input for component areas (10.0, D059)
MATCH (i:Issue)
RETURN i.id AS id, i.product_id AS product_id, i.component AS component, i.reported_ms AS reported_ms
LIMIT $limit
