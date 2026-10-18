// count_linked_chunks.cypher — how many of the given chunk ids exist and hang off an artifact
MATCH (c:Chunk)
WHERE c.id IN $ids AND (c)<-[:HAS_CHUNK]-(:Artifact)
RETURN count(c) AS n
LIMIT 1
