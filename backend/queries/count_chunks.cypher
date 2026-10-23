// count_chunks.cypher — how many chunks the graph holds: whether a product's vector search may scan its chunks exactly or must start from the index (22.6, D141)
MATCH (c:Chunk)
RETURN count(c) AS n
LIMIT 1
