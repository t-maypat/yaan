// link_context_urls.cypher — the loaded PRs, docs and links a new artifact's urls name: the live linker's context, looked up per artifact instead of read from a growing log (D113)
UNWIND $urls AS u
MATCH (a:Artifact {url: u})
WHERE a.kind IN ['pr', 'doc', 'link']
RETURN a.id AS id, a.kind AS kind, a.url AS url, a.repo AS repo, a.number AS number,
       a.product_id AS product_id
LIMIT $limit
