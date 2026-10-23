// derived_count.cypher — how many derived nodes a product has (claims, issues, extracted decisions), one row each: the fold trusts its memory of the last write only when the count is what it left (22.7, D142)
MATCH (c:Claim {product_id: $product_id})
RETURN count(c) AS n
LIMIT 1
UNION ALL
MATCH (i:Issue {product_id: $product_id})
RETURN count(i) AS n
LIMIT 1
UNION ALL
MATCH (d:Decision {product_id: $product_id})
WHERE d.origin = 'extracted'
RETURN count(d) AS n
LIMIT 1
