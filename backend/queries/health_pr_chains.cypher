// health_pr_chains.cypher — PRs with and without a status decision chain, internal vs external
MATCH (p:PR)
OPTIONAL MATCH (d:Decision)-[:AFFECTS]->(p)
WITH p, count(d) AS decisions
RETURN p.external AS external, count(p) AS prs,
       toInteger(sum(CASE WHEN decisions > 0 THEN 1 ELSE 0 END)) AS with_chain
ORDER BY external
LIMIT 10
