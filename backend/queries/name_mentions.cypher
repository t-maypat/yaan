// name_mentions.cypher — artifacts whose text still names a person (after erasure: listed for a human to review, never rewritten, since a name can be a namesake's) (F2, D125)
MATCH (a:Artifact)
WHERE a.text CONTAINS $name
RETURN a.id AS id
LIMIT $limit
