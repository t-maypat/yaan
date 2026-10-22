// person_mentions.cypher — the artifacts that @-mention a person (the linker's MENTIONS edges), whose words erasing them rewrites (F2, D125)
MATCH (p:Person {id: $id})<-[:MENTIONS]-(a:Artifact)
RETURN a.id AS id
LIMIT $limit
