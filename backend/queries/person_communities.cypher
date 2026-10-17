// person_communities.cypher — FalkorDB's native label propagation (CDLP) over COLLAB: the working groups people actually form, to compare with the org chart (10.4, D064)
CALL algo.labelPropagation({nodeLabels: ['Person'], relationshipTypes: ['COLLAB']}) YIELD node, communityId
RETURN node.id AS id, communityId AS community
LIMIT $limit
