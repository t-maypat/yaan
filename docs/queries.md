# Cypher queries

Every query Yaan runs is one parameterized statement in its own file under [`backend/queries/`](../backend/queries/), with a fixture test. This list is generated from each file's first line by [`scripts/query_table.py`](../scripts/query_table.py); a test fails when it goes stale.

FalkorDB features these queries rely on: Cypher over a property graph, range indexes on ids, a full-text index (English stemming) and an HNSW vector index on chunks, native `algo.pageRank`, `algo.betweenness`, `algo.labelPropagation` and `shortestPath`, `GRAPH.RO_QUERY` with a read-only ACL user, per-query timeouts, and `GRAPH.COPY` for the resettable demo graph.

<!-- queries -->
205 queries, one parameterized statement per file in [`backend/queries/`](../backend/queries/), each with a fixture test.

**Schema (indexes, applied and verified by `lore schema`)** (1)

| Query | Purpose |
|---|---|
| [`00_schema.cypher`](../backend/queries/00_schema.cypher) | indexes for one graph; `lore schema` runs it one line per call, then checks the result (CONTEXT §9.14; verified on 6.0.1 in Phase 1.5) |

**Search (full-text and vector, inside FalkorDB)** (3)

| Query | Purpose |
|---|---|
| [`search_fulltext.cypher`](../backend/queries/search_fulltext.cypher) | keyword hits on chunk text (higher score = better), with filters |
| [`search_vector.cypher`](../backend/queries/search_vector.cypher) | nearest chunks by embedding (score = cosine distance, lower = closer, A3), with filters |
| [`search_vector_scoped.cypher`](../backend/queries/search_vector_scoped.cypher) | exact nearest chunks within one product (cosine distance, lower = closer, A3), with filters (D042) |

**Graph health** (9)

| Query | Purpose |
|---|---|
| [`health_dead_letters.cypher`](../backend/queries/health_dead_letters.cypher) | items connectors couldn't ingest, per source and reason: each waits in `yaan connectors dead` (H1, D107) |
| [`health_duplicate_ids.cypher`](../backend/queries/health_duplicate_ids.cypher) | ids held by more than one node (a label-set mistake, A16) |
| [`health_edges.cypher`](../backend/queries/health_edges.cypher) | edge count per relationship type |
| [`health_isolated.cypher`](../backend/queries/health_isolated.cypher) | nodes with no edge at all, per label set |
| [`health_labels.cypher`](../backend/queries/health_labels.cypher) | node count per label set |
| [`health_links.cypher`](../backend/queries/health_links.cypher) | linking totals: mentions found, unresolved ones, artifacts with notes (S10) |
| [`health_mentions.cypher`](../backend/queries/health_mentions.cypher) | MENTIONS edges per target label (S10) |
| [`health_pr_chains.cypher`](../backend/queries/health_pr_chains.cypher) | PRs with and without a status decision chain, internal vs external |
| [`health_spoke_in.cypher`](../backend/queries/health_spoke_in.cypher) | resolved meeting speakers per resolution method (S10) |

**Writes: ingestion, linking, the fold, derived layers, the Sentinel** (101)

| Query | Purpose |
|---|---|
| [`clear_areas.cypher`](../backend/queries/clear_areas.cypher) | remove the derived expertise layer (Area nodes with their IN_AREA and KNOWS edges) before it is rebuilt; nothing else is touched |
| [`clear_claim_edges.cypher`](../backend/queries/clear_claim_edges.cypher) | the edges a changed claim owns, removed before the fold links them again (D101) |
| [`clear_collab.cypher`](../backend/queries/clear_collab.cypher) | remove the COLLAB projection before it is rebuilt (10.1) |
| [`clear_communities.cypher`](../backend/queries/clear_communities.cypher) | remove the Community nodes and their IN_COMMUNITY edges before they are rebuilt (10.4) |
| [`clear_decision_edges.cypher`](../backend/queries/clear_decision_edges.cypher) | the edges a changed extracted decision owns (not CAUSED or PRECEDENT_FOR, which the Sentinel and derive own), removed before the fold links them again (D101) |
| [`clear_issue_edges.cypher`](../backend/queries/clear_issue_edges.cypher) | the edges a changed issue owns (not IN_AREA, which derive owns), removed before the fold links them again (D101) |
| [`clear_issue_fixes.cypher`](../backend/queries/clear_issue_fixes.cypher) | the FIXES edges into a changed issue (the fold owns them, though they start at the PR), removed before the fold links them again (D101) |
| [`clear_pr_actions.cypher`](../backend/queries/clear_pr_actions.cypher) | a product's PRs whose stated action the fold no longer derives lose it (D101) |
| [`clear_similar_precedents.cypher`](../backend/queries/clear_similar_precedents.cypher) | remove the similarity PRECEDENT_FOR edges before they are rebuilt; precedents written by approvals (11.4) carry no basis and stay |
| [`clear_uses.cypher`](../backend/queries/clear_uses.cypher) | remove the derived Company-USES-Product edges before they are rebuilt (10.6) |
| [`delete_claims.cypher`](../backend/queries/delete_claims.cypher) | claims the fold no longer derives (their events were retracted or re-resolved), removed with their edges (D101) |
| [`delete_dead_letters.cypher`](../backend/queries/delete_dead_letters.cypher) | drop dead letters whose item has now been ingested (H1, D107) |
| [`delete_decisions.cypher`](../backend/queries/delete_decisions.cypher) | decisions the fold no longer derives (their events were retracted or re-resolved), removed with their edges (D101) |
| [`delete_issues.cypher`](../backend/queries/delete_issues.cypher) | issues the fold no longer derives (their events were retracted or re-resolved), removed with their edges (D101) |
| [`derive_uses.cypher`](../backend/queries/derive_uses.cypher) | (Company)-[:USES {issues, customers, first_ms, last_ms}]->(Product): a company uses a product when its customers report issues in it (S9, 10.6, D066) |
| [`derive_works_on.cypher`](../backend/queries/derive_works_on.cypher) | (Team)-[:WORKS_ON {stage, from_ms, to_ms, artifacts}]->(Product): when each team authored the product's artifacts per stage (channel stage, else artifact kind) (S9, 8.0 / R1-2) |
| [`link_action_for.cypher`](../backend/queries/link_action_for.cypher) | (Action)-[:FOR]->(Alert): the alert a draft answers |
| [`link_action_targets_artifact.cypher`](../backend/queries/link_action_targets_artifact.cypher) | (Action)-[:TARGETS]->(Artifact): the PR a comment goes on, or the message a correction goes under |
| [`link_action_targets_issue.cypher`](../backend/queries/link_action_targets_issue.cypher) | (Action)-[:TARGETS]->(Issue): the issue a ticket comment goes on |
| [`link_action_targets_person.cypher`](../backend/queries/link_action_targets_person.cypher) | (Action)-[:TARGETS]->(Person): who a notification goes to |
| [`link_affects.cypher`](../backend/queries/link_affects.cypher) | (Decision)-[:AFFECTS]->(Artifact), what a structural decision is about |
| [`link_affects_issue.cypher`](../backend/queries/link_affects_issue.cypher) | (Decision)-[:AFFECTS]->(Issue): what an issue-status decision is about |
| [`link_alert_caused.cypher`](../backend/queries/link_alert_caused.cypher) | re-link CAUSED from each answered alert's flagged decision to its response after a fold rebuild (the fold recreates the flagged decision under the same id) |
| [`link_alert_evidence.cypher`](../backend/queries/link_alert_evidence.cypher) | (Alert)-[:EVIDENCED_BY {quote}]->(Artifact): the message that triggered the alert, with its verbatim quote |
| [`link_alert_flags.cypher`](../backend/queries/link_alert_flags.cypher) | (Alert)-[:FLAGS]->(Decision): the decision whose change the alert is about, re-linked after every fold rebuild (decision ids are stable) |
| [`link_alert_flags_claim.cypher`](../backend/queries/link_alert_flags_claim.cypher) | (Alert)-[:FLAGS]->(Claim): the conflicting claim behind a conflict alert, re-linked after every fold rebuild (claim ids are stable) |
| [`link_asserted_by.cypher`](../backend/queries/link_asserted_by.cypher) | (Claim)-[:ASSERTED_BY]->(Person): who said it (the message's sender) |
| [`link_assigned_to.cypher`](../backend/queries/link_assigned_to.cypher) | (Issue)-[:ASSIGNED_TO {from_ms, to_ms}]->(Person): who was asked to take the issue, and when |
| [`link_attended.cypher`](../backend/queries/link_attended.cypher) | (Person)-[:ATTENDED]->(Meeting) from participants |
| [`link_authored.cypher`](../backend/queries/link_authored.cypher) | (Person)-[:AUTHORED]->(Artifact) |
| [`link_caused.cypher`](../backend/queries/link_caused.cypher) | (Decision)-[:CAUSED]->(Decision): the change that led to a response decision (11.4) |
| [`link_channel_members.cypher`](../backend/queries/link_channel_members.cypher) | (Person)-[:MEMBER_OF]->(Channel): who may read a private channel in org mode (H1, D109) |
| [`link_claim_about_issue.cypher`](../backend/queries/link_claim_about_issue.cypher) | (Claim)-[:ABOUT]->(Issue): the issue a lifecycle claim is about |
| [`link_claim_about_pr.cypher`](../backend/queries/link_claim_about_pr.cypher) | (Claim)-[:ABOUT]->(PR): the pull request a PR-intent claim is about |
| [`link_claim_from.cypher`](../backend/queries/link_claim_from.cypher) | (Claim)-[:FROM {quote, start, end}]->(Artifact): the message a claim's verbatim quote comes from (S6) |
| [`link_conflicts_with.cypher`](../backend/queries/link_conflicts_with.cypher) | (Claim)-[:CONFLICTS_WITH {reason}]->(Decision): an unauthorized claim that did not change state (§9.4, R1-3) |
| [`link_decided_by.cypher`](../backend/queries/link_decided_by.cypher) | (Decision)-[:DECIDED_BY]->(Person) |
| [`link_evidenced_by.cypher`](../backend/queries/link_evidenced_by.cypher) | (Decision)-[:EVIDENCED_BY {quote, field, start, end}]->(Artifact), the source of a decision |
| [`link_fixes.cypher`](../backend/queries/link_fixes.cypher) | (PR)-[:FIXES]->(Issue): the pull request a resolution message points to |
| [`link_in_area_issue.cypher`](../backend/queries/link_in_area_issue.cypher) | (Issue)-[:IN_AREA]->(Area): the component area an issue was reported in |
| [`link_in_area_pr.cypher`](../backend/queries/link_in_area_pr.cypher) | (PR)-[:IN_AREA]->(Area): the work item a pull request belongs to, or the component its fixed issue is in |
| [`link_in_channel.cypher`](../backend/queries/link_in_channel.cypher) | (Artifact)-[:IN_CHANNEL]->(Channel) |
| [`link_in_community.cypher`](../backend/queries/link_in_community.cypher) | (Person)-[:IN_COMMUNITY]->(Community): the working group a person belongs to (10.4) |
| [`link_in_product.cypher`](../backend/queries/link_in_product.cypher) | (Artifact)-[:IN_PRODUCT]->(Product) |
| [`link_issue_in_product.cypher`](../backend/queries/link_issue_in_product.cypher) | (Issue)-[:IN_PRODUCT]->(Product) |
| [`link_member_of.cypher`](../backend/queries/link_member_of.cypher) | (Person)-[:MEMBER_OF]->(Team) |
| [`link_mentions_company.cypher`](../backend/queries/link_mentions_company.cypher) | (Artifact)-[:MENTIONS]->(Company) by company name (S4) |
| [`link_mentions_customer.cypher`](../backend/queries/link_mentions_customer.cypher) | (Artifact)-[:MENTIONS]->(Customer) by CUST id or unambiguous name (S4) |
| [`link_mentions_person.cypher`](../backend/queries/link_mentions_person.cypher) | (Artifact)-[:MENTIONS]->(Person) from @eid_ mentions (S4) |
| [`link_mentions_product.cypher`](../backend/queries/link_mentions_product.cypher) | (Artifact)-[:MENTIONS {surfaces}]->(Product) by name or codename (S4) |
| [`link_notifies.cypher`](../backend/queries/link_notifies.cypher) | (Alert)-[:NOTIFIES {reasons}]->(Person): the people in the alert's blast radius, with why each one is in it |
| [`link_part_of.cypher`](../backend/queries/link_part_of.cypher) | (Team)-[:PART_OF]->(Org) |
| [`link_precedent_for.cypher`](../backend/queries/link_precedent_for.cypher) | (Decision)-[:PRECEDENT_FOR]->(Decision): an earlier response to the same kind of alert, from older to newer (11.4) |
| [`link_reported_by.cypher`](../backend/queries/link_reported_by.cypher) | (Issue)-[:REPORTED_BY]->(Customer): the customer a report names (CUST id) |
| [`link_reported_in.cypher`](../backend/queries/link_reported_in.cypher) | (Issue)-[:REPORTED_IN]->(Artifact): the message that raised the issue |
| [`link_reports_to.cypher`](../backend/queries/link_reports_to.cypher) | (Person)-[:REPORTS_TO]->(Person) from the hierarchy |
| [`link_reverts.cypher`](../backend/queries/link_reverts.cypher) | (PR)-[:REVERTS]->(PR): a "Revert PR# N" pull request and the PR N it undoes, in the same repository (S4, D047) |
| [`link_reviewed.cypher`](../backend/queries/link_reviewed.cypher) | (Person)-[:REVIEWED {state, ts_ms, comment}]->(PR), one edge per review |
| [`link_shares.cypher`](../backend/queries/link_shares.cypher) | (Artifact)-[:SHARES {url}]->(Artifact): a PR, doc or link whose url appears in the text (S4) |
| [`link_similar_precedent.cypher`](../backend/queries/link_similar_precedent.cypher) | (earlier Decision)-[:PRECEDENT_FOR {basis, similarity}]->(later Decision): the same kind of decision, taken earlier, on a similar issue or PR (10.6, D066) |
| [`link_spoke_in.cypher`](../backend/queries/link_spoke_in.cypher) | (Person)-[:SPOKE_IN {turns, method}]->(Meeting or MeetingChat), speaker names resolved (S4, D037) |
| [`link_supersedes.cypher`](../backend/queries/link_supersedes.cypher) | (Decision)-[:SUPERSEDES]->(Decision), newer to older in one topic |
| [`link_supports.cypher`](../backend/queries/link_supports.cypher) | (Claim)-[:SUPPORTS]->(Decision): a claim that created or repeated the decision's value (§9.4) |
| [`link_works_at.cypher`](../backend/queries/link_works_at.cypher) | (Customer)-[:WORKS_AT]->(Company) |
| [`mark_action_sent.cypher`](../backend/queries/mark_action_sent.cypher) | record where an approved action was delivered (a URL in the connected tool) and when, so it is sent once |
| [`set_action_status.cypher`](../backend/queries/set_action_status.cypher) | record what happened to each drafted action (approved, skipped, dismissed), with the edited text when the approver changed it |
| [`set_alert_response.cypher`](../backend/queries/set_alert_response.cypher) | the last step of a response: a 'responding' alert closes as approved or dismissed, with who and when (11.4, D110); matches nothing unless the response was claimed, so a second response is refused |
| [`set_channel_visibility.cypher`](../backend/queries/set_channel_visibility.cypher) | who may read a channel in org mode: 'public' (everyone signed in) or 'private' (its MEMBER_OF people); from Slack, or declared in connectors.yaml (H1, D109) |
| [`set_claim_hash.cypher`](../backend/queries/set_claim_hash.cypher) | the hash of the rows that wrote a claim, stamped last, so an interrupted write is redone by the next fold (D101) |
| [`set_decision_hash.cypher`](../backend/queries/set_decision_hash.cypher) | the hash of the rows that wrote a decision, stamped last, so an interrupted write is redone by the next fold (D101) |
| [`set_issue_hash.cypher`](../backend/queries/set_issue_hash.cypher) | the hash of the rows that wrote a issue, stamped last, so an interrupted write is redone by the next fold (D101) |
| [`set_link_notes.cypher`](../backend/queries/set_link_notes.cypher) | per artifact: mentions found by linking and the unresolved ones (S4 -> S10) |
| [`set_person_betweenness.cypher`](../backend/queries/set_person_betweenness.cypher) | store each person's COLLAB betweenness score and rank (10.4) |
| [`set_person_pagerank.cypher`](../backend/queries/set_person_pagerank.cypher) | store each person's COLLAB PageRank score and rank (10.4) |
| [`set_pr_action.cypher`](../backend/queries/set_pr_action.cypher) | materialize a PR's current stated action (to_merge / to_close / on_hold / reverted) and its reason from the `pr:<id>/action` chain |
| [`set_product_truck.cypher`](../backend/queries/set_product_truck.cypher) | a product's truck factor over its areas (10.3, D059): TF, the people removed in order, how many areas have an author, and how many lose all of them |
| [`upsert_actions.cypher`](../backend/queries/upsert_actions.cypher) | drafted follow-ups for an alert (11.3): ticket_comment, doc_fix, notify_owner; a new draft starts proposed, and a re-run rewrites the text only while it is still proposed |
| [`upsert_alerts.cypher`](../backend/queries/upsert_alerts.cypher) | Sentinel Alert nodes, one per alert-worthy decision change (11.1); a new alert starts open, and a re-run never resets its status or created time |
| [`upsert_areas.cypher`](../backend/queries/upsert_areas.cypher) | Area nodes (10.0, D059): a product's bug-report components and development work items, the units of expertise and of the truck factor |
| [`upsert_channels.cypher`](../backend/queries/upsert_channels.cypher) | Slack channel nodes with their stage (planning / dev / support) |
| [`upsert_chunks.cypher`](../backend/queries/upsert_chunks.cypher) | search chunks with their embeddings, linked to their artifact (S3) |
| [`upsert_claims.cypher`](../backend/queries/upsert_claims.cypher) | Claim nodes: one verified extracted event each, with the fold's outcome and the policy verdict (S5-S8); observed_ms is its event's, when Lore first learned it (D101) |
| [`upsert_collab.cypher`](../backend/queries/upsert_collab.cypher) | (Person)-[:COLLAB {w, reviews, meetings, chats}]->(Person), both directions: how much two people worked together (10.1, D060) |
| [`upsert_communities.cypher`](../backend/queries/upsert_communities.cypher) | Community nodes (working groups found by CDLP over COLLAB) with their size and how they map onto org-chart teams (10.4, D064) |
| [`upsert_companies.cypher`](../backend/queries/upsert_companies.cypher) | company nodes from the customers' `company` field |
| [`upsert_customers.cypher`](../backend/queries/upsert_customers.cypher) | customer nodes from metadata/customers_data.json |
| [`upsert_dead_letters.cypher`](../backend/queries/upsert_dead_letters.cypher) | items a connector couldn't ingest, with the reason and the item itself so `yaan connectors retry` can replay them (H1, D107) |
| [`upsert_decisions.cypher`](../backend/queries/upsert_decisions.cypher) | decision nodes (one per state in a topic's chain); an extracted one carries its product and its creating event's observed_ms, a structural one keeps when it was first seen (D101) |
| [`upsert_docs.cypher`](../backend/queries/upsert_docs.cypher) | document artifacts, with type and version |
| [`upsert_events.cypher`](../backend/queries/upsert_events.cypher) | verified events, the derived layer's system of record (D101): immutable; observed_ms is when Lore first learned one, and an event asserted again after a retraction is live again |
| [`upsert_issues.cypher`](../backend/queries/upsert_issues.cypher) | canonical Issue nodes from extracted reports, keyed by normalized title (S7, D049); status from the fold |
| [`upsert_knows.cypher`](../backend/queries/upsert_knows.cypher) | (Person)-[:KNOWS {doa_abs, doa_norm, author, deliveries, first, discussion, score, first_ms, last_ms, refs}]->(Area): degree-of-authorship expertise with recency (10.2, §9.7) |
| [`upsert_links.cypher`](../backend/queries/upsert_links.cypher) | shared link artifacts, one per address (D023) |
| [`upsert_meeting_chats.cypher`](../backend/queries/upsert_meeting_chats.cypher) | meeting chat artifacts |
| [`upsert_meetings.cypher`](../backend/queries/upsert_meetings.cypher) | meeting transcript artifacts |
| [`upsert_messages.cypher`](../backend/queries/upsert_messages.cypher) | Slack message artifacts |
| [`upsert_orgs.cypher`](../backend/queries/upsert_orgs.cypher) | org nodes from the hierarchy's `org` field |
| [`upsert_people.cypher`](../backend/queries/upsert_people.cypher) | person nodes from metadata/employee.json or people.yaml, with an explicit authority when people.yaml gives one (D102) |
| [`upsert_products.cypher`](../backend/queries/upsert_products.cypher) | product nodes from channel names, with codenames found in artifacts (D029) |
| [`upsert_prs.cypher`](../backend/queries/upsert_prs.cypher) | pull request artifacts, with current_status from their decision chain |
| [`upsert_teams.cypher`](../backend/queries/upsert_teams.cypher) | team nodes, one per VP group in the hierarchy (D026) |

**Reads: agent tools, the API, the Sentinel, connectors, permissions** (91)

| Query | Purpose |
|---|---|
| [`acquire_lock.cypher`](../backend/queries/acquire_lock.cypher) | take a graph's writer lock if it is free or its holder's lease ran out: one statement, so two writers can't both get it (D101) |
| [`actions_to_send.cypher`](../backend/queries/actions_to_send.cypher) | approved drafted actions not yet sent to a connected tool, of responses that finished (D110), oldest decision first, with what they target (17.2 write-back) |
| [`alert_actions.cypher`](../backend/queries/alert_actions.cypher) | the drafted actions for one alert, for the Inbox: ticket comment first, then the correction, then notifications |
| [`alert_precedents.cypher`](../backend/queries/alert_precedents.cypher) | earlier responses (approved or dismissed) to alerts of the same kind in the same product, newest first: the PRECEDENT_FOR sources of a new response (11.4, §9.5) |
| [`all_derived.cypher`](../backend/queries/all_derived.cypher) | every derived node (claims, issues, extracted decisions), for the one-time rewrite of a graph whose derived layer predates D101 |
| [`area_connectors.cypher`](../backend/queries/area_connectors.cypher) | people who collaborate (COLLAB) with anyone who knows the given areas, ranked as bridges first (betweenness), then by PageRank: who to route a question through when no expert is left (10.7, D064) |
| [`area_evidence.cypher`](../backend/queries/area_evidence.cypher) | the pull requests each person authored in the given areas: the quotable evidence behind an expertise claim (10.7) |
| [`area_experts.cypher`](../backend/queries/area_experts.cypher) | the people who KNOW the given areas, best score first: degree-of-authorship, author flag, deliveries, discussion, recency score, PageRank rank (10.7) |
| [`area_issue_people.cypher`](../backend/queries/area_issue_people.cypher) | who worked on each issue and how (10.2): `fix` = authored a PR that FIXES it, `resolved` = stated its resolution, `talk` = any other claim about it |
| [`area_issues.cypher`](../backend/queries/area_issues.cypher) | every canonical issue with its product and component, the input for component areas (10.0, D059) |
| [`area_owners.cypher`](../backend/queries/area_owners.cypher) | the listed owners of the given areas: who the area's issues are assigned to, with each issue's status and its report message (where the assignment is asked, to cite), to compare with who actually knows it (10.7) |
| [`area_prs.cypher`](../backend/queries/area_prs.cypher) | the internal pull requests with author, time, the PR each one reverts and the issue it fixes: the input for work-item areas (10.0, D059) |
| [`area_reviews.cypher`](../backend/queries/area_reviews.cypher) | every review of an internal pull request: who, which PR, when (10.1 COLLAB, 10.2 discussion signal) |
| [`artifact_access.cypher`](../backend/queries/artifact_access.cypher) | what the permission policy needs per artifact: its kind, its channel and that channel's visibility, for viewer-scoped answers (W7, D109) |
| [`artifact_people.cypher`](../backend/queries/artifact_people.cypher) | author and participant ids of given artifacts (metadata for search results) |
| [`artifact_people_detail.cypher`](../backend/queries/artifact_people_detail.cypher) | the people around one artifact: authors, attendees, speakers, reviewers (with review state) and people it mentions |
| [`artifact_texts.cypher`](../backend/queries/artifact_texts.cypher) | every quotable text of the given artifacts (title, url, text, chunk spans), for the citation validator |
| [`artifacts_present.cypher`](../backend/queries/artifacts_present.cypher) | which of these ids are artifacts in the graph: the live path's "already ingested?" and the demo's injected flags (D101) |
| [`channel_context.cypher`](../backend/queries/channel_context.cypher) | the messages before a new one in its channel, newest first: the conversation the live extraction reads it in, as the batch extraction does (D102) |
| [`channel_members.cypher`](../backend/queries/channel_members.cypher) | the people a channel lists as members (org mode), so a membership sync changes only what changed (H1, D109) |
| [`claim_alert_response.cypher`](../backend/queries/claim_alert_response.cypher) | the first step of a response: an open alert becomes 'responding' and stores every write the response will make, so a crash midway is finished, not left half-done (H2, D110); matches nothing when the alert isn't open |
| [`collab_meetings.cypher`](../backend/queries/collab_meetings.cypher) | who attended each meeting: the co-attendance part of COLLAB (10.1) |
| [`collab_messages.cypher`](../backend/queries/collab_messages.cypher) | who wrote each (non-system) Slack message and in which channel: the conversation part of COLLAB (10.1) |
| [`collab_paths.cypher`](../backend/queries/collab_paths.cypher) | the shortest COLLAB path (at most 4 hops) between each given pair of people: how a listed owner reaches the real expert, or how close a backup already is to a key person (10.5, D065) |
| [`connection_path.cypher`](../backend/queries/connection_path.cypher) | the shortest chain of people who work together (COLLAB, at most 6 hops) between two people, with each hop's tie: weight, shared reviews, meetings and chats (tool 13 connection_path, R2, D065) |
| [`count_linked_chunks.cypher`](../backend/queries/count_linked_chunks.cypher) | how many of the given chunk ids exist and hang off an artifact |
| [`decision_chain.cypher`](../backend/queries/decision_chain.cypher) | every state in the given decision topics: value, validity window, origin, who decided it, the evidence quotes, the claims behind it with the policy's verdict, refused claims, alerts that flagged it, the responses it caused and similar earlier cases; oldest first (tool 6 decision_lineage, R2) |
| [`decision_precedents.cypher`](../backend/queries/decision_precedents.cypher) | the earlier decisions that are precedents for the given one (similar subject, same kind of decision), most similar first, with who decided and the evidence quote (10.6, D066) |
| [`derived_sources.cypher`](../backend/queries/derived_sources.cypher) | the source artifacts of derived nodes (issues, claims, decisions, alerts): a viewer may see a derived node only if they may see one of its sources (D100). A decision's source is its evidence; what it AFFECTS is its subject, and counts only for a structural decision read from that record. Each branch binds its node by id, then WITH, then traverses: without the WITH the planner folds the MATCHes into an edge scan per id (60x slower) |
| [`derived_state.cypher`](../backend/queries/derived_state.cypher) | a product's derived nodes now in the graph with the hash of the rows that wrote them: the fold writes only what changed and removes only what went stale (D101) |
| [`directory_entries.cypher`](../backend/queries/directory_entries.cypher) | customers and people by id, with a customer's company and a person's role: the "metadata as documents" baseline's directory (D103), the joins the graph makes, handed to plain RAG as text |
| [`doc_discussion.cypher`](../backend/queries/doc_discussion.cypher) | the Slack discussion of a document: messages in the channel where it was first shared, until a newer version is shared there (or $window_ms) |
| [`doc_meetings.cypher`](../backend/queries/doc_meetings.cypher) | the product's meetings whose transcript names the document's type, with who spoke in each |
| [`entity_card.cypher`](../backend/queries/entity_card.cypher) | one person, customer, company, product, issue or area by id: its labels, properties and relationship types (counted in the API), for the UI's entity panel |
| [`event_products.cypher`](../backend/queries/event_products.cypher) | the products that have events: the batch fold folds each of them (D101) |
| [`evidence_subgraph.cypher`](../backend/queries/evidence_subgraph.cypher) | the evidence around cited artifacts for the UI: authors, product, customers mentioned, the claims quoting them, what those claims are about (issue -> customer -> company) and the decisions they support |
| [`find_docs.cypher`](../backend/queries/find_docs.cypher) | a product's documents matching an id, a type ("Market Research Report") or words of the title, with their authors |
| [`find_missing.cypher`](../backend/queries/find_missing.cypher) | absence of a path, by kind: resolved issues no PR FIXES, open issues no PR FIXES, issues never ASSIGNED_TO anyone, own PRs nobody REVIEWED, areas only one author holds (no backup); each row with an artifact to cite (tool 8 find_missing, R2) |
| [`fold_customers.cypher`](../backend/queries/fold_customers.cypher) | every customer id: a report's customer links only to a known one (the fold's context, D101) |
| [`fold_people.cypher`](../backend/queries/fold_people.cypher) | every person's role, for the authority policy (the fold's context, read from the graph, D101) |
| [`fold_product_active.cypher`](../backend/queries/fold_product_active.cypher) | the people who authored or reviewed anything in a product: "works on the product" for the policy (the fold's context, D101) |
| [`fold_product_prs.cypher`](../backend/queries/fold_product_prs.cypher) | a product's pull requests with author and reviewers: PR references resolve to them and the policy reads them (the fold's context, D101) |
| [`get_alert.cypher`](../backend/queries/get_alert.cypher) | one alert with its facts, and the person who would respond to it (approver), before an approve or dismiss (11.4) |
| [`get_artifact.cypher`](../backend/queries/get_artifact.cypher) | one artifact's full text, its channel, and its other quotable fields (PR reviews, doc feedback) from its chunks |
| [`get_channel.cypher`](../backend/queries/get_channel.cypher) | one channel by id: the live path checks that a new message's channel exists before writing it (11.6) |
| [`impact_of.cypher`](../backend/queries/impact_of.cypher) | blast radius of one decision (tool 11, §9.5, D040): for its issue, the fixing PRs with their authors and reviewers, the reporting customers and their companies, assignees, other issues on the same component; for its PR, the author, reviewers and the issues it fixes; the evidence and decider of the state it replaced; WORKS_ON teams; CAUSED decisions ≤ 3 hops. One row per item: kind, id, name, role, why, via (the id it hangs off), detail, quote, artifact_id, ts_iso |
| [`issue_fix_prs.cypher`](../backend/queries/issue_fix_prs.cypher) | the pull requests that fix one issue, for a connector that comments on them (17.4 write-back) |
| [`issue_report_ids.cypher`](../backend/queries/issue_report_ids.cypher) | the messages that reported one issue, oldest first, so a connector can find the ticket it came from (17.5 write-back) |
| [`issue_reports.cypher`](../backend/queries/issue_reports.cypher) | customer issue reports: support-channel messages in the product that mention a customer, with each customer's company and the people mentioned |
| [`key_people.cypher`](../backend/queries/key_people.cypher) | each product's first truck-factor person and the areas they are the only author of, in any product: the input for key_person alerts (11.5, D062) |
| [`legacy_derived.cypher`](../backend/queries/legacy_derived.cypher) | extracted decisions written before D101 carry no product_id: a graph holding them gets its derived layer cleared once and rewritten |
| [`lineage_subjects.cypher`](../backend/queries/lineage_subjects.cypher) | the product's issues and own pull requests, the subjects a decision chain can be about: decision_lineage and impact_of match a name, title or PR number against them (R2) |
| [`links_shared.cypher`](../backend/queries/links_shared.cypher) | links shared in the product's messages, with who shared them and where |
| [`list_alerts.cypher`](../backend/queries/list_alerts.cypher) | the Inbox: alerts, newest change first, optionally by status, product, alert id or triggering artifact, with who they notify |
| [`list_dead_letters.cypher`](../backend/queries/list_dead_letters.cypher) | the items connectors couldn't ingest, newest failure first, for status, retry and Graph health (H1, D107) |
| [`list_indexes.cypher`](../backend/queries/list_indexes.cypher) | every index in the graph with its kind, options and build status (lore schema checks these against 00_schema.cypher) |
| [`list_products.cypher`](../backend/queries/list_products.cypher) | every product with its codenames and how many artifacts it holds, for the UI's product picker |
| [`outside_decisions.cypher`](../backend/queries/outside_decisions.cypher) | active dismissals decided by someone whose working group (CDLP community) is not among the groups of the people who did the work in the issue's area (11.5, D064); rows shaped like sentinel_changes |
| [`people_by_role.cypher`](../backend/queries/people_by_role.cypher) | people with a role (from the org chart) who authored, spoke in or attended the product's artifacts, most active first |
| [`person_activity.cypher`](../backend/queries/person_activity.cypher) | what one person did per product: artifacts authored, reviewed, attended or spoken in, by kind (tool 4 person_profile, R2) |
| [`person_betweenness.cypher`](../backend/queries/person_betweenness.cypher) | FalkorDB's native betweenness centrality over COLLAB: the people who bridge otherwise separate groups (10.4, D064) |
| [`person_communities.cypher`](../backend/queries/person_communities.cypher) | FalkorDB's native label propagation (CDLP) over COLLAB: the working groups people actually form, to compare with the org chart (10.4, D064) |
| [`person_links.cypher`](../backend/queries/person_links.cypher) | around one person, one row per item: areas they know (KNOWS, best first), closest collaborators (COLLAB weight), open issues assigned to them, and their latest artifacts in the product as evidence (tool 4 person_profile, R2) |
| [`person_pagerank.cypher`](../backend/queries/person_pagerank.cypher) | FalkorDB's native PageRank over the COLLAB projection: well-connected people, the "connectors" who can route a question when an area has no expert left (10.4) |
| [`person_profile.cypher`](../backend/queries/person_profile.cypher) | one person: role, location, org-chart team and manager, working-group community, and their PageRank / betweenness ranks over COLLAB (tool 4 person_profile, R2) |
| [`person_radius.cypher`](../backend/queries/person_radius.cypher) | what depends on one person (11.5, D062): the areas they are the only author of, the people who also know those areas (backups) and their manager; one row per item in impact_of's shape |
| [`person_teams.cypher`](../backend/queries/person_teams.cypher) | each person's team from the org chart (MEMBER_OF), to compare with the working groups (10.4, D064) |
| [`person_truck.cypher`](../backend/queries/person_truck.cypher) | the products whose truck factor removes this person: losing them leaves most of that product's areas with no author (tool 4 person_profile, 10.3) |
| [`precedent_candidates.cypher`](../backend/queries/precedent_candidates.cypher) | every extracted decision that isn't a plain report, with what it is about (issue title + description, or PR title): the input for PRECEDENT_FOR by similarity (10.6, D066) |
| [`product_areas.cypher`](../backend/queries/product_areas.cypher) | a product's areas (components and work items) with their names, aliases and authors: who_knows matches a topic against them (10.7) |
| [`product_clock.cypher`](../backend/queries/product_clock.cypher) | each product's last artifact time: "now" on HERB's clock, for recency (10.2, D057) |
| [`product_events.cypher`](../backend/queries/product_events.cypher) | one product's events as Lore knows them now (not retracted), or as it knew them at $known_at_ms (observed by then, not yet retracted): the fold's input (D101) |
| [`product_issues.cypher`](../backend/queries/product_issues.cypher) | the product's canonical customer issues with current status, status history (claims with quotes, and the policy's reason when a claim was refused), assignee, reporting customers and fixing PRs (slice B, D049, D057) |
| [`product_prs.cypher`](../backend/queries/product_prs.cypher) | the product's own pull requests (external ones left out) with their record status chain, author, reviews, the PRs that revert them or that they revert, and what the team said to do with them in Slack (action claims with quotes) |
| [`product_truck.cypher`](../backend/queries/product_truck.cypher) | a product's truck factor and the people it removes, for who_knows' key-person flag (10.3, 10.7) |
| [`rank_companies.cypher`](../backend/queries/rank_companies.cypher) | the companies (or customers) whose customers reported a product's issues, with counts per current status and each issue's title, status and report message as evidence; the agent's ranking by issues reported / unresolved / resolved / reopened / dismissed (tool 7 rank, R2) |
| [`rank_people.cypher`](../backend/queries/rank_people.cypher) | engineers of a product ranked by one metric, counted in the graph: PRs authored / merged / reviewed / approved / sent back, bugs fixed (authored a PR that FIXES an issue), open bugs assigned; each row carries up to 5 counted items as evidence: the PR (or the issue's report message) with its title, to cite (tool 7 rank, R2) |
| [`release_lock.cypher`](../backend/queries/release_lock.cypher) | give a graph's writer lock back, only if this holder still has it (D101) |
| [`renew_lock.cypher`](../backend/queries/renew_lock.cypher) | extend a graph's writer lock, only while this holder still has it: a long write keeps its lease, a lost one stays lost (H2, D110) |
| [`resolve_names.cypher`](../backend/queries/resolve_names.cypher) | people, products, customers or companies whose name matches the words (full-text on Entity.name), most active in the product first |
| [`resolve_product.cypher`](../backend/queries/resolve_product.cypher) | products whose id, name or codename equals the text (codenames come from renamed channels, D029) |
| [`retract_events.cypher`](../backend/queries/retract_events.cypher) | a newer version of an artifact no longer states these events: close them (retracted_ms), never delete them, so the belief at an earlier moment can be replayed (D101) |
| [`sentinel_changes.cypher`](../backend/queries/sentinel_changes.cypher) | the decision changes the Sentinel judges (11.1): every extracted supersession (old and new value, subject, evidence quote, decider) and every claim that CONFLICTS_WITH an active decision (§9.4 rule 5) |
| [`states_at.cypher`](../backend/queries/states_at.cypher) | the decisions in force at one moment for a product's issues and own PRs (valid_from_ms <= t < valid_to_ms, or still open): the as-of view of list_issues, list_prs and decision_lineage (R2) |
| [`stuck_responses.cypher`](../backend/queries/stuck_responses.cypher) | responses claimed but never closed (the process stopped midway), with the writes they still owe (H2, D110) |
| [`subject_product.cypher`](../backend/queries/subject_product.cypher) | the product an issue or pull request belongs to, for the API's lineage endpoint |
| [`unlink_channel_members.cypher`](../backend/queries/unlink_channel_members.cypher) | a person left a private channel (or was removed): they stop seeing it (H1, D109) |
| [`viewer_channels.cypher`](../backend/queries/viewer_channels.cypher) | the channels a person belongs to (they posted there), for viewer-scoped answers (W7) |
| [`viewer_meetings.cypher`](../backend/queries/viewer_meetings.cypher) | the meetings a person attended or spoke in, for viewer-scoped answers (W7) |
| [`viewer_member_channels.cypher`](../backend/queries/viewer_member_channels.cypher) | the channels a person is a member of (org mode: from Slack or connectors.yaml), for viewer-scoped answers (H1, D109) |
| [`withdraw_alerts.cypher`](../backend/queries/withdraw_alerts.cypher) | open alerts whose change is gone after a rebuild (e.g. a corrected extraction) become 'withdrawn'; never deleted, and approved or dismissed ones keep their status |
<!-- /queries -->
