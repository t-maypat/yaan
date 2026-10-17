// upsert_communities.cypher — Community nodes (working groups found by CDLP over COLLAB) with their size and how they map onto org-chart teams (10.4, D064)
UNWIND $rows AS r
MERGE (c:Community {id: r.id})
SET c.size = r.size, c.teams = r.teams, c.team_count = r.team_count, c.top_team = r.top_team,
    c.top_team_share = r.top_team_share, c.label = r.label
