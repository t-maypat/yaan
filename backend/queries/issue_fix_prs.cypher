// issue_fix_prs.cypher — the pull requests that fix one issue, for a connector that comments on them (17.4 write-back)
MATCH (pr:PR)-[:FIXES]->(:Issue {id: $issue_id})
RETURN pr.id AS id, pr.url AS url
ORDER BY pr.id
LIMIT $limit
