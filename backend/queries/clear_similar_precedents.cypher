// clear_similar_precedents.cypher — remove the similarity PRECEDENT_FOR edges an earlier rebuild left (written before run $run), after this one wrote its own: readers never see none (D112); precedents written by approvals (11.4) carry no basis and stay
MATCH (:Decision)-[e:PRECEDENT_FOR {basis: 'similar_subject'}]->(:Decision)
WHERE coalesce(e.run, 0) <> $run
DELETE e
