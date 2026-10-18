// artifact_people.cypher — author and participant ids of given artifacts (metadata for search results)
UNWIND $ids AS id
MATCH (a:Artifact {id: id})
OPTIONAL MATCH (au:Person)-[:AUTHORED]->(a)
WITH a, collect(DISTINCT au.id) AS authors
OPTIONAL MATCH (pa:Person)-[:ATTENDED]->(a)
RETURN a.id AS artifact_id, authors, collect(DISTINCT pa.id) AS participants
LIMIT 200
