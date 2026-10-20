// product_aliases.cypher — a product's codenames (from renamed channels), so the live linker finds "abAIX" as CollaborateForce (D113)
MATCH (p:Product {id: $product_id})
RETURN p.aliases AS aliases
LIMIT 1
