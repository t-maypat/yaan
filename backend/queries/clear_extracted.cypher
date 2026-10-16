// clear_extracted.cypher — remove the derived extraction layer (Claims, Issues, extracted Decisions) before the fold rebuilds it from every product's events; source artifacts and structural decisions stay
MATCH (n)
WHERE n:Claim OR n:Issue OR (n:Decision AND n.origin = 'extracted')
DETACH DELETE n
