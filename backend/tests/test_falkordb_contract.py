"""FalkorDB contract tests: one test per assumption A1-A17 in docs/decisions.md (Phase 1.7).

CONTEXT.md §3.6 was researched against the 4.x docs; these pin what the pinned server (6.0.1)
actually does. They double as upgrade guards: rerun them whenever FALKORDB_TAG changes, and
any behavior the code relies on that moved will fail here first, by row number.
"""

import json
import time
from pathlib import Path

import pytest
from falkordb import FalkorDB
from redis.exceptions import NoPermissionError, ResponseError

from app import db
from app.config import REPO_ROOT, get_settings

pytestmark = pytest.mark.db

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def admin(falkordb) -> FalkorDB:
    """The `default` user, for server settings the app users may not read."""
    s = get_settings()
    if not s.falkordb_admin_password:
        pytest.fail("FALKORDB_ADMIN_PASSWORD is not set in .env", pytrace=False)
    return FalkorDB(host=s.falkordb_host, port=s.falkordb_port, password=s.falkordb_admin_password)


def writer(graph):
    return db.client(readonly=False).select_graph(graph)


def reader(graph):
    return db.client(readonly=True).select_graph(graph)


def read(graph, text, params=None, timeout=2000):
    return reader(graph).ro_query(text, params, timeout=timeout).result_set


def unit(dim: int, *hot: tuple[int, float]) -> list[float]:
    v = [0.0] * dim
    for i, x in hot:
        v[i] = x
    return v


# --- A1 / A2 / A9 / A10: Cypher surface -------------------------------------------------------


def test_a01_relationship_type_alternation_works(graph):
    rows = read(
        graph,
        "MATCH (p:Person)-[r:AUTHORED|REVIEWED]->(:PR) "
        "RETURN p.name, type(r) ORDER BY p.name LIMIT 5",
    )
    assert rows == [["Ada", "AUTHORED"], ["Grace", "REVIEWED"]]


def test_a02_pattern_predicates_in_where_work_but_exists_subqueries_dont(graph):
    no_authored = "MATCH (p:Person) WHERE NOT (p)-[:AUTHORED]->() RETURN p.name LIMIT 5"
    assert read(graph, no_authored) == [["Grace"]]
    assert read(graph, "MATCH (p:Person) WHERE (p)-[:AUTHORED]->(:PR) RETURN p.name LIMIT 5") == [
        ["Ada"]
    ]
    with pytest.raises(ResponseError, match="Invalid input"):
        read(
            graph,
            "MATCH (p:Person) WHERE NOT EXISTS { MATCH (p)-[:AUTHORED]->() } RETURN p.name LIMIT 5",
        )


def test_a09_label_alternation_is_unsupported(graph):
    with pytest.raises(ResponseError, match="Invalid input '\\|'"):
        read(graph, "MATCH (n:Person|Team) RETURN count(n) LIMIT 1")


def test_a10_regex_works_on_6_0(graph):
    # 4.x docs said unsupported; 6.0.1 supports it. Yaan still avoids it (lint ban, D021).
    assert read(graph, "MATCH (p:Person) WHERE p.name =~ 'A.*' RETURN p.name LIMIT 5") == [["Ada"]]


# --- A3 / A15: vectors --------------------------------------------------------------------------


def load_chunks(graph, chunks, embed=True):
    writer(graph).query(
        "UNWIND $rows AS r CREATE (c:Chunk {id: r.id, text: r.text, embedding: "
        + ("vecf32(r.v)" if embed else "r.v")
        + "})",
        {"rows": chunks},
    )


def test_a03_vector_score_is_a_cosine_distance_lower_is_closer(empty_graph):
    dim = get_settings().yaan_embed_dim
    load_chunks(
        empty_graph,
        [
            {"id": "same", "text": "", "v": unit(dim, (0, 1.0))},
            {"id": "near", "text": "", "v": unit(dim, (0, 0.9), (1, 0.1))},
            {"id": "far", "text": "", "v": unit(dim, (1, 1.0))},
        ],
    )
    rows = read(
        empty_graph,
        "CALL db.idx.vector.queryNodes('Chunk', 'embedding', 3, vecf32($v)) YIELD node, score "
        "RETURN node.id, score LIMIT 3",
        {"v": unit(dim, (0, 1.0))},
    )
    assert [r[0] for r in rows] == ["same", "near", "far"]  # already ordered, closest first
    scores = [r[1] for r in rows]
    assert scores[0] == pytest.approx(0.0, abs=1e-6) and scores[2] == pytest.approx(1.0, abs=1e-6)
    assert scores[0] < scores[1] < scores[2]


def test_a15_vectors_must_be_written_with_vecf32(empty_graph):
    dim = get_settings().yaan_embed_dim
    load_chunks(empty_graph, [{"id": "vec", "text": "", "v": unit(dim, (0, 1.0))}])
    load_chunks(empty_graph, [{"id": "list", "text": "", "v": unit(dim, (0, 1.0))}], embed=False)
    query = (
        "CALL db.idx.vector.queryNodes('Chunk', 'embedding', 10, vecf32($v)) YIELD node "
        "RETURN node.id LIMIT 10"
    )
    # A plain list is stored as a List and silently left out of the vector index.
    assert read(empty_graph, query, {"v": unit(dim, (0, 1.0))}) == [["vec"]]
    with pytest.raises(ResponseError, match=f"dimension mismatch, expected {dim} but got 3"):
        read(empty_graph, query, {"v": [1.0, 0.0, 0.0]})
    distance = read(
        empty_graph,
        "MATCH (c:Chunk {id: 'vec'}) RETURN vec.cosineDistance(c.embedding, vecf32($v)) LIMIT 1",
        {"v": unit(dim, (1, 1.0))},
    )
    assert distance[0][0] == pytest.approx(1.0, abs=1e-6)


# --- A11: full-text -----------------------------------------------------------------------------

TEXTS = [
    {"id": "c1", "text": "We reverted the login change after the outage", "v": None},
    {"id": "c2", "text": "Login is fast now; revert not needed", "v": None},
    {"id": "c3", "text": "Quarterly planning for the Orion roadmap", "v": None},
    {"id": "c4", "text": "The release was rolled back on Friday", "v": None},
]
FULLTEXT = (
    "CALL db.idx.fulltext.queryNodes('Chunk', $q) YIELD node, score "
    "RETURN node.id, score ORDER BY score DESC LIMIT 10"
)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("revert", {"c1", "c2"}),  # stemming: "reverted" matches
        ("revert*", {"c1", "c2"}),  # prefix
        ("%revrt%", {"c2"}),  # fuzzy, one edit away
        ('"rolled back"', {"c4"}),  # exact phrase
        ("login orion", set()),  # several words = AND
        ("login | orion", {"c1", "c2", "c3"}),  # explicit OR
        ("login -fast", {"c1"}),  # NOT
        ("LOGIN", {"c1", "c2"}),  # case-insensitive
        ("roadmaps", {"c3"}),  # plural stemmed
        ("the", {"c1", "c3", "c4"}),  # no stopword list on 6.0.1: common words are indexed
    ],
)
def test_a11_fulltext_query_syntax(empty_graph, query, expected):
    writer(empty_graph).query(
        "UNWIND $rows AS r CREATE (:Chunk {id: r.id, text: r.text})", {"rows": TEXTS}
    )
    assert {row[0] for row in read(empty_graph, FULLTEXT, {"q": query})} == expected


def test_a11_fulltext_higher_score_is_more_relevant(empty_graph):
    writer(empty_graph).query(
        "UNWIND $rows AS r CREATE (:Chunk {id: r.id, text: r.text})", {"rows": TEXTS}
    )
    rows = read(empty_graph, FULLTEXT, {"q": "revert"})
    assert rows[0][0] == "c2" and rows[0][1] > rows[1][1]  # exact word beats the stemmed one


# --- A4 / A5: algorithms and paths ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "call", "columns"),
    [
        ("pageRank", "algo.pageRank('Person', 'MEMBER_OF')", "node, score"),
        ("pageRank-all", "algo.pageRank(null, null)", "node, score"),
        (
            "betweenness",
            "algo.betweenness({nodeLabels: ['Person', 'Team'], relationshipTypes: ['MEMBER_OF']})",
            "node, score",
        ),
        (
            "labelPropagation",
            "algo.labelPropagation({nodeLabels: ['Person', 'Team'], "
            "relationshipTypes: ['MEMBER_OF'], maxIterations: 10})",
            "node, communityId",
        ),
        (
            "WCC",
            "algo.WCC({nodeLabels: ['Person', 'Team'], relationshipTypes: ['MEMBER_OF']})",
            "node, componentId",
        ),
        ("HarmonicCentrality", "algo.HarmonicCentrality()", "node, score"),
    ],
)
def test_a04_algorithm_signatures(graph, name, call, columns):
    first = columns.split(", ")[1]
    rows = read(graph, f"CALL {call} YIELD {columns} RETURN node.id, {first} LIMIT 20")
    assert rows, f"{name} returned nothing"
    assert all(isinstance(r[1], int | float) for r in rows)


def test_a04_pagerank_takes_label_and_type_not_a_config_map(graph):
    with pytest.raises(ResponseError, match="expected at least 2"):
        read(graph, "CALL algo.pageRank({nodeLabels: ['Person']}) YIELD node RETURN node LIMIT 1")


def test_a04_bfs_returns_reached_nodes_without_the_source(graph):
    rows = read(
        graph,
        "MATCH (a:Person {id: 'eid_ada'}) CALL algo.BFS(a, 2, 'MEMBER_OF') YIELD nodes, edges "
        "RETURN [n IN nodes | n.id], size(edges) LIMIT 1",
    )
    assert rows == [[["team_core"], 1]]


def test_a05_shortest_paths(graph):
    with pytest.raises(ResponseError, match="only supports shortestPaths in WITH or RETURN"):
        read(
            graph,
            "MATCH (a:Person {id: 'eid_ada'}), (b:Person {id: 'eid_linus'}) "
            "MATCH p = shortestPath((a)-[*..6]-(b)) RETURN length(p) LIMIT 1",
        )
    in_with = read(
        graph,
        "MATCH (a:Decision {value: 'reverted'}), (b:Product {id: 'prod_acme'}) "
        "WITH shortestPath((a)-[*..6]->(b)) AS p RETURN length(p), [n IN nodes(p) | n.id] LIMIT 1",
    )
    assert in_with == [[2, ["dec:pr:42/status:1760086400000", "pr:42", "prod_acme"]]]
    with pytest.raises(ResponseError, match="Source and destination must already be resolved"):
        read(
            graph,
            "MATCH (a:Person {id: 'eid_ada'}), (b:Product {id: 'prod_acme'}) "
            "MATCH p = allShortestPaths((a)-[*..6]->(b)) RETURN length(p) LIMIT 5",
        )
    all_paths = read(  # the endpoints must be bound by an earlier clause (WITH)
        graph,
        "MATCH (a:Person {id: 'eid_ada'}), (b:Product {id: 'prod_acme'}) WITH a, b "
        "MATCH p = allShortestPaths((a)-[*..6]->(b)) RETURN [n IN nodes(p) | n.id] LIMIT 5",
    )
    assert sorted(all_paths) == [
        [["eid_ada", "pr:42", "prod_acme"]],
        [["eid_ada", "team_core", "prod_acme"]],
    ]
    sp = read(
        graph,
        "MATCH (a:Person {id: 'eid_ada'}), (b:Product {id: 'prod_acme'}) "
        "CALL algo.SPpaths({sourceNode: a, targetNode: b, relTypes: ['MEMBER_OF', 'WORKS_ON'], "
        "maxLen: 6}) YIELD path, pathWeight, pathCost RETURN length(path), pathWeight LIMIT 5",
    )
    assert sp == [[2, 2]]
    none = read(
        graph,
        "MATCH (a:Person {id: 'eid_grace'}), (b:Person {id: 'eid_linus'}) "
        "RETURN shortestPath((a)-[:MEMBER_OF*..4]->(b)) IS NULL LIMIT 1",
    )
    assert none == [[True]]


# --- A6 / A16: identity -------------------------------------------------------------------------


def constraint(graph, label, prop):
    return writer(graph).execute_command(
        "GRAPH.CONSTRAINT", "CREATE", graph, "UNIQUE", "NODE", label, "PROPERTIES", 1, prop
    )


def test_a06_unique_constraint_needs_an_index_and_is_enforced(empty_graph):
    with pytest.raises(ResponseError, match="missing supporting exact-match index"):
        constraint(empty_graph, "Team", "name")  # no index on Team.name
    assert constraint(empty_graph, "Person", "id") == "PENDING"  # Person.id is indexed
    status_query = "CALL db.constraints() YIELD label, status RETURN status LIMIT 5"
    deadline = time.monotonic() + 5
    while read(empty_graph, status_query) != [["OPERATIONAL"]] and time.monotonic() < deadline:
        time.sleep(0.1)
    assert read(empty_graph, status_query) == [["OPERATIONAL"]]
    w = writer(empty_graph)
    w.query("CREATE (:Person:Entity {id: 'eid_1'})")
    with pytest.raises(ResponseError, match="unique constraint violation"):
        w.query("CREATE (:Person:Entity {id: 'eid_1'})")
    assert w.query("MERGE (p:Person:Entity {id: 'eid_1'}) RETURN p.id").result_set == [["eid_1"]]


def test_a16_merge_with_a_different_label_set_creates_a_duplicate(empty_graph):
    w = writer(empty_graph)
    w.query("MERGE (:Person {id: 'eid_x'})")
    w.query("MERGE (:Person:Entity {id: 'eid_x'})")  # doesn't match the Person-only node
    w.query("MERGE (:Person:Entity {id: 'eid_x'})")  # matches the Person:Entity one
    assert read(empty_graph, "MATCH (p:Person {id: 'eid_x'}) RETURN count(p) LIMIT 1") == [[2]]


def test_a18_a_pattern_predicate_then_match_needs_with_between(empty_graph):
    """Found 13 Oct (D044). On 6.0.1, `WHERE (m)-[:R]->(:L)` directly followed by a MATCH that
    binds a new variable errors ("'c' not defined"); in a larger query (issue_reports, first
    draft) the same shape silently matched every Customer instead of the one mentioned. A WITH
    between the two clauses gives the right answer. If this test fails, the engine changed:
    re-check the lint rule in test_query_lint.py."""
    writer(empty_graph).query(
        "CREATE (m:Message {id: 'm1'})-[:MENTIONS]->(:Customer {id: 'c1'}), (:Customer {id: 'c2'})"
    )
    head = "MATCH (m:Message {id: 'm1'}) WHERE (m)-[:MENTIONS]->(:Customer) "
    tail = "MATCH (m)-[:MENTIONS]->(c:Customer) RETURN count(c) LIMIT 1"
    with pytest.raises(ResponseError, match="'c' not defined"):
        read(empty_graph, head + tail)
    assert read(empty_graph, head + "WITH m " + tail) == [[1]]


def test_a19_aggregate_by_node_then_read_properties(empty_graph):
    """Found 13 Oct (D045). On 6.0.1 this crashes the whole server (panic in reply_compact,
    src/reply.rs:655, "unwrap() on a None value"), so it can't run here:
        CREATE (:A {id: 'x'}), (:A {id: 'y', m1: 1})
        MATCH (a:A {id: 'x'}) RETURN a.id, a.m1, count(a)
    i.e. an aggregate grouped by a property that this node lacks but others have. The forms
    Yaan uses instead (lint rule A19 in test_query_lint.py) are pinned here."""
    writer(empty_graph).query("CREATE (:A {id: 'x'}), (:A {id: 'y', m1: 1, m2: 2})")
    by_node = "MATCH (a:A {id: 'x'}) WITH a, count(a) AS n RETURN a.id, a.m1, a.m2, n LIMIT 1"
    assert read(empty_graph, by_node) == [["x", None, None, 1]]
    keyed = "MATCH (a:A) RETURN coalesce(a.m1, 0) AS m1, count(a) ORDER BY m1 LIMIT 5"
    assert read(empty_graph, keyed) == [[0, 1], [1, 1]]


# --- A7 / A8 / A17: the server ------------------------------------------------------------------


def config(admin, key):
    return admin.connection.execute_command("CONFIG", "GET", key)[1]


def graph_config(admin, key):
    return admin.connection.execute_command("GRAPH.CONFIG", "GET", key)[1]


def test_a07_data_lives_on_the_volume_and_snapshots_within_60_s(admin):
    # Survival across down/up and a hard kill was proven by hand (decisions.md A7, D013).
    assert config(admin, "dir") == "/var/lib/falkordb/data"
    assert config(admin, "save") == "60 1"
    assert config(admin, "appendonly") == "no"


def test_a08_server_runs_the_pinned_engine(admin):
    env = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    tag = next(
        line.split("=", 1)[1] for line in env.splitlines() if line.startswith("FALKORDB_TAG=")
    )
    major, minor, patch = (int(x) for x in tag.strip().split("."))
    modules = {m["name"]: m for m in admin.connection.module_list()}
    assert modules["graph"]["ver"] == major * 10000 + minor * 100 + patch  # 6.0.1 -> 60001


def test_a12_reads_cannot_write_through_either_lock(graph):
    with pytest.raises(ResponseError, match="to be executed only on read-only queries"):
        reader(graph).ro_query("CREATE (:Hack)")
    with pytest.raises(NoPermissionError, match="'graph.QUERY' command"):
        reader(graph).query("MATCH (n) RETURN count(n)")  # even a pure read
    for command in (("GRAPH.DELETE", graph), ("GRAPH.PROFILE", graph, "RETURN 1"), ("DEL", graph)):
        with pytest.raises(NoPermissionError):
            db.client(readonly=True).connection.execute_command(*command)
    with pytest.raises(NoPermissionError):
        db.client(readonly=False).connection.execute_command("FLUSHALL")


def test_a13_memory_per_graph(admin, graph):
    # The real number (dev products, Phase 3.9) is in decisions.md A13; this pins the command.
    reply = admin.connection.execute_command("GRAPH.MEMORY", "USAGE", graph)
    sizes = dict(zip(reply[::2], reply[1::2], strict=False))
    assert int(sizes["total_graph_sz_mb"]) >= 0


def test_a14_python_client_timeout_and_read_only_path(graph):
    with pytest.raises(ResponseError, match="Query timed out"):
        reader(graph).ro_query("UNWIND range(1, 300000000) AS i RETURN count(i)", timeout=100)
    from falkordb.asyncio import FalkorDB as AsyncFalkorDB  # exists; Yaan uses the sync client

    assert AsyncFalkorDB


def test_a17_server_limits(admin, empty_graph):
    assert graph_config(admin, "TIMEOUT_DEFAULT") == 10000
    assert graph_config(admin, "TIMEOUT_MAX") == 120000
    assert graph_config(admin, "RESULTSET_SIZE") == -1
    with pytest.raises(ResponseError, match="cannot exceed the TIMEOUT_MAX"):
        read(empty_graph, "RETURN 1", timeout=120001)
    rows = read(empty_graph, "UNWIND range(1, 20000) AS i RETURN i LIMIT 20000")
    assert len(rows) == 20000  # no silent cap
    # A write ignores its own timeout: asked for 1 ms, it still finishes (~0.3 s).
    result = writer(empty_graph).query("UNWIND range(1, 200000) AS i CREATE (:T {i: i})", timeout=1)
    assert result.nodes_created == 200000


def test_a20_optional_match_where_after_a_collect_drops_every_row(empty_graph):
    """Found 16 Oct (D050). On 6.0.1, after `OPTIONAL MATCH ... WITH x, collect(...)`, a second
    `OPTIONAL MATCH ... WHERE <property test>` matches nothing, though the same clause without
    the earlier collect works (product_prs lost every PR's status chain). Filter inside the
    aggregate instead: collect(CASE WHEN <test> THEN v END), which skips the nulls."""
    writer(empty_graph).query(
        "CREATE (p:P {id: 'p'}), (:U)-[:R]->(p), (:D {k: 'a', v: 1})-[:A]->(p), "
        "(:D {k: 'b', v: 2})-[:A]->(p)"
    )
    head = "MATCH (p:P {id: 'p'}) OPTIONAL MATCH (u:U)-[:R]->(p) WITH p, collect(u) AS us "
    broken = head + "OPTIONAL MATCH (d:D)-[:A]->(p) WHERE d.k = 'a' RETURN collect(d.v) LIMIT 1"
    fixed = (
        head + "OPTIONAL MATCH (d:D)-[:A]->(p) "
        "RETURN collect(CASE WHEN d.k = 'a' THEN d.v END) LIMIT 1"
    )
    assert read(empty_graph, broken) == [[[]]]
    assert read(empty_graph, fixed) == [[[1]]]


def test_a21_two_computed_grouping_keys_give_every_aggregate_the_first_ones_value(empty_graph):
    """Found 17 Oct (D059). On 6.0.1, when a projection groups by two or more computed keys
    (properties, coalesce(...)), every aggregate in it returns the first aggregate's value:
    count(*) and sum(x) both come back as the count. One computed key, or keys projected in a
    WITH first, give the right numbers."""
    writer(empty_graph).query(
        "CREATE (:A {p: 'x', k: 'c', d: 4}), (:A {p: 'x', k: 'c', d: 5}), "
        "(:A {p: 'y', k: 'w', d: 2})"
    )
    broken = (
        "MATCH (a:A) RETURN a.p AS p, a.k AS k, count(*) AS n, sum(a.d) AS s ORDER BY p LIMIT 5"
    )
    fixed = (
        "MATCH (a:A) WITH a.p AS p, a.k AS k, a "
        "RETURN p, k, count(a) AS n, sum(a.d) AS s ORDER BY p LIMIT 5"
    )
    one_key = "MATCH (a:A) RETURN a.p AS p, count(*) AS n, sum(a.d) AS s ORDER BY p LIMIT 5"
    assert [r[2:] for r in read(empty_graph, broken)] == [[2, 2], [1, 1]]  # s should be 9, 2
    assert [r[2:] for r in read(empty_graph, fixed)] == [[2, 9], [1, 2]]
    assert [r[1:] for r in read(empty_graph, one_key)] == [[2, 9], [1, 2]]


def test_a22_a_node_id_filter_below_zero_matches_no_node(empty_graph):
    """Found 21 Oct (D120). On 6.0.1, `WHERE id(n) > -1` (a literal or a parameter) returns no
    node at all, though every node id is >= 0; on relationships the same filter works. Page
    nodes with `id(n) >= $from`, starting at 0."""
    writer(empty_graph).query("CREATE (:A {i: 1})-[:R]->(:A {i: 2})")
    assert read(empty_graph, "MATCH (n) WHERE id(n) > -1 RETURN id(n) LIMIT 5") == []
    assert len(read(empty_graph, "MATCH (n) WHERE id(n) >= 0 RETURN id(n) LIMIT 5")) == 2
    assert len(read(empty_graph, "MATCH ()-[e]->() WHERE id(e) > -1 RETURN id(e) LIMIT 5")) == 1


def test_a23_tojson_round_trips_a_nodes_properties(empty_graph):
    """Verified 23 Oct (D129). `toJSON(properties(n))` returns one JSON string whose parse
    equals `properties(n)`: escapes, quotes, control characters, emoji, 16-digit integers,
    floats, booleans and lists. The fold reads its events this way: the Python client decodes
    a map value by value, about 5x slower than json.loads of the string."""
    tricky = "He said \"fix it\" \ now\nnext\ttab — ünïcødé 🐛 \u0001 'single'"
    writer(empty_graph).query(
        "CREATE (:A {s: $s, big: 1767229542000123, f: 0.1, neg: -3, b: true, l: ['a', 1, 2.5]})",
        {"s": tricky},
    )
    (j, p) = read(empty_graph, "MATCH (n:A) RETURN toJSON(properties(n)), properties(n) LIMIT 1")[0]
    assert json.loads(j) == dict(p) and json.loads(j)["s"] == tricky
