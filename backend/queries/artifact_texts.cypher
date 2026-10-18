// artifact_texts.cypher — every quotable text of the given artifacts (title, url, text, chunk spans), for the citation validator
UNWIND $ids AS id
MATCH (a:Artifact {id: id})
OPTIONAL MATCH (a)-[:HAS_CHUNK]->(c:Chunk)
WITH a, collect(c.text) AS chunks
RETURN a.id AS id, a.title AS title, a.url AS url, a.text AS text, chunks
LIMIT 200
