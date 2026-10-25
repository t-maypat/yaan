"""Minimal repros of the four FalkorDB 6.x bugs Yaan found (docs/falkordb-bugs.md, A18-A21).

Run them against a throwaway server, never the Yaan database (A19 crashes the server):

    docker run -d --rm --name fdb-test -p 127.0.0.1:6390:6379 falkordb/falkordb:6.0.2
    uv run --project backend python scripts/falkordb_repros.py 6390            # A19 last
    uv run --project backend python scripts/falkordb_repros.py 6390 --skip-a19

Each bug prints the broken query's result next to the workaround's. On 4.22.1 both agree.
"""

import sys

from falkordb import FalkorDB


def main(port: int, skip_a19: bool) -> int:
    db = FalkorDB(host="127.0.0.1", port=port)
    module = [
        m for m in db.connection.execute_command("MODULE", "LIST") if m[1] == "graph"
    ]
    print("graph module version:", module[0][3] if module else "?")

    def fresh(name: str, setup: str):
        g = db.select_graph(name)
        if name in db.list_graphs():
            g.delete()
        g.query(setup)
        return g

    def run(g, q: str):
        try:
            return g.ro_query(q).result_set
        except Exception as e:  # noqa: BLE001 - a repro reports the server's error
            return f"ERROR {type(e).__name__}: {e}"

    g = fresh("a18", "CREATE (:P {id: 1})-[:R]->(:Q), (:C {id: 'c1'}), (:C {id: 'c2'})")
    print(
        "A18 broken:",
        run(g, "MATCH (p:P) WHERE (p)-[:R]->() MATCH (c:C {id: 'c1'}) RETURN c.id"),
    )
    print(
        "A18 with WITH:",
        run(
            g,
            "MATCH (p:P) WHERE (p)-[:R]->() WITH p MATCH (c:C {id: 'c1'}) RETURN c.id",
        ),
    )

    g = fresh(
        "a20",
        "CREATE (p:P {id: 'p'}), (:U)-[:R]->(p), (:D {k: 'a', v: 1})-[:A]->(p), "
        "(:D {k: 'b', v: 2})-[:A]->(p)",
    )
    head = (
        "MATCH (p:P {id: 'p'}) OPTIONAL MATCH (u:U)-[:R]->(p) WITH p, collect(u) AS us "
    )
    print(
        "A20 broken:",
        run(
            g,
            head + "OPTIONAL MATCH (d:D)-[:A]->(p) WHERE d.k = 'a' RETURN collect(d.v)",
        ),
    )
    print(
        "A20 filter in aggregate:",
        run(
            g,
            head
            + "OPTIONAL MATCH (d:D)-[:A]->(p) RETURN collect(CASE WHEN d.k = 'a' THEN d.v END)",
        ),
    )

    g = fresh(
        "a21",
        "CREATE (:A {p: 'x', k: 'c', d: 4}), (:A {p: 'x', k: 'c', d: 5}), (:A {p: 'y', k: 'w', d: 2})",
    )
    print(
        "A21 broken:",
        run(
            g,
            "MATCH (a:A) RETURN a.p AS p, a.k AS k, count(*) AS n, sum(a.d) AS s ORDER BY p",
        ),
    )
    print(
        "A21 keys in WITH:",
        run(
            g,
            "MATCH (a:A) WITH a.p AS p, a.k AS k, a RETURN p, k, count(a) AS n, sum(a.d) AS s ORDER BY p",
        ),
    )
    for name in ("a18", "a20", "a21"):
        db.select_graph(name).delete()

    if not skip_a19:
        g = fresh("a19", "CREATE (:A {id: 'x'}), (:A {id: 'y', m1: 1})")
        print(
            "A19 (may crash the server):",
            run(g, "MATCH (a:A {id: 'x'}) RETURN a.id, a.m1, count(a)"),
        )
        try:
            db.connection.ping()
            print("A19: server still up")
            db.select_graph("a19").delete()
        except Exception as e:  # noqa: BLE001 - the crash is the finding
            print("A19: server down:", e)
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]), "--skip-a19" in sys.argv))
