// erase_mentions.cypher — replace an erased person's @-mention in what others wrote (artifact, chunks, events) with a placeholder of the same length, so quote offsets still hold (F2, D125)
MATCH (a:Artifact)
WHERE a.id IN $ids
OPTIONAL MATCH (a)-[:HAS_CHUNK]->(c:Chunk)
SET a.text = replace(a.text, $mention, $placeholder),
    c.text = CASE WHEN c IS NULL THEN null ELSE replace(c.text, $mention, $placeholder) END
