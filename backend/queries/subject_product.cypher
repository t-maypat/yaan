// subject_product.cypher — the product an issue or pull request belongs to, for the API's lineage endpoint
OPTIONAL MATCH (i:Issue {id: $id})
OPTIONAL MATCH (a:Artifact {id: $id})
RETURN coalesce(i.product_id, a.product_id) AS product_id
LIMIT 1
