// health_spoke_in.cypher — resolved meeting speakers per resolution method (S10)
MATCH (:Person)-[s:SPOKE_IN]->(:Artifact)
RETURN s.method AS method, count(s) AS n
ORDER BY method
LIMIT 20
