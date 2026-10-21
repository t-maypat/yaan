// health_unverified.cypher — status changes applied though the speaker's authority is unknown (no role or authority in people.yaml), per product: who to list in people.yaml (D117)
MATCH (c:Claim {authority: 'unverified'})
WITH coalesce(c.product_id, '?') AS product, c
WITH product, count(c) AS n
RETURN product, n
ORDER BY n DESC
LIMIT 100
