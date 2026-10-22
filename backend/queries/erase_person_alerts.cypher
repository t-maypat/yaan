// erase_person_alerts.cypher — alerts and drafts that name an erased person: their id goes, and their name and @-mention become placeholders (F2, D125)
MATCH (a:Alert)
WHERE a.actor_id = $id OR a.quote CONTAINS $mention OR a.summary CONTAINS $name
SET a.actor_id = CASE WHEN a.actor_id = $id THEN null ELSE a.actor_id END,
    a.actor_name = CASE WHEN a.actor_id IS NULL AND a.actor_name = $name THEN $former ELSE a.actor_name END,
    a.quote = replace(a.quote, $mention, $placeholder),
    a.summary = replace(a.summary, $name, $former)
RETURN a.id AS id
