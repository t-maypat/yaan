// resolve_product.cypher — products whose id, name or codename equals the text (codenames come from renamed channels, D029)
MATCH (p:Product)
WHERE toLower(p.id) = toLower($text) OR toLower(p.name) = toLower($text)
   OR any(alias IN coalesce(p.aliases, []) WHERE toLower(alias) = toLower($text))
RETURN p.id AS id, p.name AS name, p.aliases AS aliases
LIMIT 5
