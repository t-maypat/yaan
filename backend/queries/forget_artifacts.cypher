// forget_artifacts.cypher — delete forgotten artifacts with every edge they had; upsert_tombstones keeps a content-free record of each (F2, D125)
MATCH (a:Artifact)
WHERE a.id IN $ids
DETACH DELETE a
