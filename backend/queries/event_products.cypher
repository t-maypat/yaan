// event_products.cypher — the products that have events: the batch fold folds each of them (D101)
MATCH (e:Event)
WITH DISTINCT e.product_id AS product_id
RETURN product_id
LIMIT $limit
