// person_profile.cypher — one person: role, location, org-chart team and manager, working-group community, and their PageRank / betweenness ranks over COLLAB (tool 4 person_profile, R2)
MATCH (p:Person {id: $id})
OPTIONAL MATCH (p)-[:MEMBER_OF]->(t:Team)
WITH p, collect(DISTINCT t.name) AS teams
OPTIONAL MATCH (p)-[:REPORTS_TO]->(m:Person)
WITH p, teams, collect(DISTINCT {id: m.id, name: m.name, role: m.role}) AS managers
OPTIONAL MATCH (p)-[:IN_COMMUNITY]->(c:Community)
WITH p, teams, managers, collect(DISTINCT c.id) AS communities
RETURN p.id AS id, p.name AS name, p.role AS role, p.location AS location, p.pagerank_rank AS pagerank_rank,
       p.betweenness_rank AS betweenness_rank, teams, managers, communities
LIMIT 1
