// erase_person_actions.cypher — drafted actions that name an erased person: their name and @-mention become placeholders (F2, D125)
MATCH (x:Action)
WHERE x.draft CONTAINS $name OR x.draft CONTAINS $mention OR x.quote CONTAINS $mention
SET x.draft = replace(replace(x.draft, $mention, $placeholder), $name, $former),
    x.quote = replace(x.quote, $mention, $placeholder)
RETURN x.id AS id
