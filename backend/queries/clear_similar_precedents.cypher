// clear_similar_precedents.cypher — remove the similarity PRECEDENT_FOR edges before they are rebuilt; precedents written by approvals (11.4) carry no basis and stay
MATCH (:Decision)-[e:PRECEDENT_FOR {basis: 'similar_subject'}]->(:Decision)
DELETE e
