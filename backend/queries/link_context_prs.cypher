// link_context_prs.cypher — the loaded PRs that new "Revert PR# N" pull requests name, by repository and number: the live linker's revert targets (D113)
UNWIND $refs AS r
MATCH (a:Artifact {repo: r.repo, number: r.number})
WHERE a.kind = 'pr'
RETURN a.id AS id, a.url AS url, a.repo AS repo, a.number AS number, a.product_id AS product_id
LIMIT $limit
