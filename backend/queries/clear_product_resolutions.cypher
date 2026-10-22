// clear_product_resolutions.cypher — forget or erase rewrote a product's events: drop up to $limit of their stored resolutions per call, so the next fold resolves everything afresh (D129)
MATCH (e:Event {product_id: $product_id})
WHERE e.res_v IS NOT NULL
WITH e LIMIT $limit
SET e.res_issue = null, e.res_how = null, e.res_v = null
RETURN count(e) AS cleared
