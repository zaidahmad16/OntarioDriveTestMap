#!/usr/bin/env python3
"""
build_intersection_index.py — offline intersection lookup and gazetteer.

DESTINATION: geometry/build_intersection_index.py

An intersection is a node shared by two or more named drivable ways.
That is a fact in the extract rather than something a geocoder has to be
persuaded to infer.

RAMPS. Grade-separated roads do not share a node with the roads they
connect to — they connect through link ways, which OSM tags
motorway_link / trunk_link / primary_link and usually leaves unnamed.
The original named-ways-only rule therefore made every ramp connection
invisible: Airport Parkway had two junctions in the whole Ottawa
extract, and six independent traces describing "airport parkway ×
walkley" were dropped as impossible.

So unnamed link ways are now followed. Chains of links are grouped into
connected components, and every named street touching any node of a
component is treated as connected to every other. These junctions are
recorded with kind='ramp' so downstream can tell a genuine intersection
from a ramp connection — they are not the same manoeuvre.

Usage:
    pip install osmium
    python3 build_intersection_index.py ottawa.osm.pbf --db ../data/osm.db
    python3 build_intersection_index.py --db ../data/osm.db \\
        --lookup "airport parkway" "walkley"
    python3 build_intersection_index.py --db ../data/osm.db \\
        --streets "walkley,cedarwood,baycrest,walkley"
"""

import argparse
import os
import re
import sqlite3
import sys
from collections import defaultdict

DRIVABLE = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential",
    "living_street", "service", "road",
}

# Unnamed ways of these types are ramps and slip roads. They carry the
# connection between two named roads that never share a node.
LINK = {"motorway_link", "trunk_link", "primary_link", "secondary_link",
        "tertiary_link", "road"}

SUFFIXES = {
    "road", "rd", "street", "st", "avenue", "ave", "drive", "dr",
    "boulevard", "blvd", "parkway", "pkwy", "crescent", "cres",
    "court", "crt", "ct", "lane", "ln", "way", "place", "pl",
    "terrace", "terr", "circle", "cir", "trail", "private",
    "north", "south", "east", "west", "n", "s", "e", "w",
}


def full_form(name):
    s = re.sub(r"[^\w\s]", " ", (name or "").lower())
    return " ".join(s.split())


def normalise(name):
    """'Cedarwood Drive' -> 'cedarwood'.

    Both forms are indexed, so a street whose suffix is part of its real
    name — Airport Parkway — is still findable under the full form even
    though the stripped form collides with something else.
    """
    words = full_form(name).split()
    while words and words[-1] in SUFFIXES:
        words.pop()
    return " ".join(words) if words else full_form(name)


SCHEMA = """
CREATE TABLE IF NOT EXISTS streets (
    name       TEXT NOT NULL,
    base       TEXT NOT NULL,
    full       TEXT NOT NULL,
    way_id     INTEGER NOT NULL,
    highway    TEXT
);
CREATE TABLE IF NOT EXISTS centres (
    centre_id  TEXT PRIMARY KEY,
    name       TEXT,
    addr       TEXT,
    lat        REAL NOT NULL,
    lon        REAL NOT NULL,
    osm_way_id INTEGER
);
CREATE TABLE IF NOT EXISTS junctions (
    node_id    INTEGER PRIMARY KEY,
    lat        REAL NOT NULL,
    lon        REAL NOT NULL,
    n_streets  INTEGER NOT NULL,
    kind       TEXT DEFAULT 'node'
);
CREATE TABLE IF NOT EXISTS junction_streets (
    node_id    INTEGER NOT NULL,
    name       TEXT NOT NULL,
    base       TEXT NOT NULL,
    full       TEXT NOT NULL,
    way_id     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_js_base ON junction_streets(base);
CREATE INDEX IF NOT EXISTS ix_js_full ON junction_streets(full);
CREATE INDEX IF NOT EXISTS ix_js_node ON junction_streets(node_id);
CREATE INDEX IF NOT EXISTS ix_streets_base ON streets(base);
"""


def build(pbf_path, db_path, keep_service=True, ramps=True):
    try:
        import osmium
    except ImportError:
        print("pyosmium not installed.  pip install osmium")
        return 1

    drivable = set(DRIVABLE)
    if not keep_service:
        drivable.discard("service")

    node_streets = defaultdict(set)     # node -> {(name, way_id)}
    way_rows = []
    link_ways = []                      # unnamed links: [(way_id, [nodes])]

    class WayPass(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.n = 0
            self.links = 0

        def way(self, w):
            hw = w.tags.get("highway")
            if hw not in drivable:
                return
            name = w.tags.get("name")
            if not name:
                if ramps and hw in LINK:
                    link_ways.append((w.id, [nd.ref for nd in w.nodes]))
                    self.links += 1
                return
            self.n += 1
            way_rows.append((name, normalise(name), full_form(name),
                             w.id, hw))
            for nd in w.nodes:
                node_streets[nd.ref].add((name, w.id))

    print(f"pass 1: reading ways from {pbf_path}")
    wp = WayPass()
    wp.apply_file(pbf_path)
    print(f"  named drivable ways: {wp.n:,}")
    print(f"  unnamed link ways:   {wp.links:,}")
    print(f"  nodes touched:       {len(node_streets):,}")

    centres = []

    class CentrePass(osmium.SimpleHandler):
        def way(self, w):
            if w.tags.get("amenity") != "driver_testing":
                return
            pts = [(n.lat, n.lon) for n in w.nodes if n.location.valid()]
            if not pts:
                return
            centres.append((
                (w.tags.get("name") or f"centre{w.id}").lower().replace(" ", ""),
                w.tags.get("name"),
                f"{w.tags.get('addr:housenumber','')} "
                f"{w.tags.get('addr:street','')}".strip(),
                sum(p[0] for p in pts) / len(pts),
                sum(p[1] for p in pts) / len(pts),
                w.id))

        def node(self, n):
            if n.tags.get("amenity") == "driver_testing" and n.location.valid():
                centres.append((
                    (n.tags.get("name") or f"centre{n.id}").lower().replace(" ", ""),
                    n.tags.get("name"), "", n.location.lat, n.location.lon, n.id))

    print("pass 1b: test centres")
    CentrePass().apply_file(pbf_path, locations=True)
    print(f"  found:               {len(centres)}")

    # A junction is a node where two or more DISTINCT street names meet.
    # Comparing names not way ids, since one street is split across many
    # ways and every split point would otherwise look like an intersection.
    junction_ids = {
        nid for nid, pairs in node_streets.items()
        if len({name for name, _ in pairs}) >= 2
    }
    print(f"  node junctions:      {len(junction_ids):,}")

    # ── ramp connections ────────────────────────────────────────────
    # Chain the unnamed links into connected components, then read off
    # which named streets each component touches. Two streets joined by
    # a chain of ramps are connected, even though no node is shared.
    ramp_pairs = []        # (repr_node, {(name, way_id)})
    if ramps and link_ways:
        parent = {}

        def find(x):
            while parent.get(x, x) != x:
                parent[x] = parent.get(parent[x], parent[x])
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for wid, nodes in link_ways:
            for n in nodes:
                parent.setdefault(n, n)
            for a, b in zip(nodes, nodes[1:]):
                union(a, b)

        comp = defaultdict(list)
        for n in parent:
            comp[find(n)].append(n)

        for root, nodes in comp.items():
            touching = set()
            for n in nodes:
                touching |= node_streets.get(n, set())
            if len({nm for nm, _ in touching}) >= 2:
                # anchor on a node that a named street actually touches,
                # so the coordinate lands on real road rather than mid-ramp
                anchor = next((n for n in nodes if n in node_streets), nodes[0])
                ramp_pairs.append((anchor, touching))
        print(f"  ramp connections:    {len(ramp_pairs):,}")

    coords = {}
    want = junction_ids | {a for a, _ in ramp_pairs}

    class NodePass(osmium.SimpleHandler):
        def node(self, n):
            if n.id in want and n.location.valid():
                coords[n.id] = (n.location.lat, n.location.lon)

    print("pass 2: reading node coordinates")
    NodePass().apply_file(pbf_path)
    print(f"  located:             {len(coords):,}")

    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)

    con.executemany(
        "INSERT INTO streets (name, base, full, way_id, highway) "
        "VALUES (?,?,?,?,?)", way_rows)

    jrows, jsrows = [], []
    for nid in junction_ids:
        if nid not in coords:
            continue
        lat, lon = coords[nid]
        pairs = node_streets[nid]
        jrows.append((nid, lat, lon, len({n for n, _ in pairs}), "node"))
        for name, wid in pairs:
            jsrows.append((nid, name, normalise(name), full_form(name), wid))

    seen = {r[0] for r in jrows}
    for anchor, touching in ramp_pairs:
        if anchor not in coords or anchor in seen:
            continue
        lat, lon = coords[anchor]
        seen.add(anchor)
        jrows.append((anchor, lat, lon, len({n for n, _ in touching}), "ramp"))
        for name, wid in touching:
            jsrows.append((anchor, name, normalise(name), full_form(name), wid))

    con.executemany(
        "INSERT OR REPLACE INTO junctions "
        "(node_id, lat, lon, n_streets, kind) VALUES (?,?,?,?,?)", jrows)
    con.executemany(
        "INSERT INTO junction_streets (node_id, name, base, full, way_id) "
        "VALUES (?,?,?,?,?)", jsrows)
    if centres:
        con.executemany(
            "INSERT OR REPLACE INTO centres "
            "(centre_id, name, addr, lat, lon, osm_way_id) VALUES (?,?,?,?,?,?)",
            centres)
    con.commit()

    n_streets = con.execute(
        "SELECT COUNT(DISTINCT base) FROM streets").fetchone()[0]
    n_ramp = con.execute(
        "SELECT COUNT(*) FROM junctions WHERE kind='ramp'").fetchone()[0]
    print(f"\nwrote {db_path}")
    print(f"  distinct street names: {n_streets:,}")
    print(f"  junctions:             {len(jrows):,}  ({n_ramp:,} via ramps)")
    con.close()
    return 0


def variants(name):
    return list({normalise(name), full_form(name)} - {""})


def lookup(db_path, a, b):
    va, vb = variants(a), variants(b)
    pa = ",".join("?" * len(va))
    pb = ",".join("?" * len(vb))
    con = sqlite3.connect(db_path)
    rows = con.execute(f"""
        SELECT j.node_id, j.lat, j.lon, j.kind,
               GROUP_CONCAT(DISTINCT js.name)
        FROM junctions j
        JOIN junction_streets js ON js.node_id = j.node_id
        WHERE j.node_id IN (SELECT node_id FROM junction_streets
                            WHERE base IN ({pa}) OR full IN ({pa}))
          AND j.node_id IN (SELECT node_id FROM junction_streets
                            WHERE base IN ({pb}) OR full IN ({pb}))
        GROUP BY j.node_id
    """, (*va, *va, *vb, *vb)).fetchall()
    con.close()
    return rows


def suggest(db_path, name, limit=6):
    con = sqlite3.connect(db_path)
    b = normalise(name)
    rows = con.execute(
        "SELECT DISTINCT base FROM streets WHERE base LIKE ? LIMIT ?",
        (f"%{b}%", limit)).fetchall()
    if not rows:
        import difflib
        allb = [r[0] for r in con.execute(
            "SELECT DISTINCT base FROM streets").fetchall()]
        rows = [(m,) for m in difflib.get_close_matches(b, allb,
                                                        n=limit, cutoff=0.7)]
    con.close()
    return [r[0] for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pbf", nargs="?")
    ap.add_argument("--db", default="../data/osm.db")
    ap.add_argument("--no-service", action="store_true")
    ap.add_argument("--no-ramps", action="store_true",
                    help="named ways only, the old behaviour")
    ap.add_argument("--lookup", nargs=2, metavar=("A", "B"))
    ap.add_argument("--streets")
    args = ap.parse_args()

    if args.lookup:
        rows = lookup(args.db, *args.lookup)
        if rows:
            for nid, lat, lon, kind, names in rows:
                via = "  (via ramp)" if kind == "ramp" else ""
                print(f"  node {nid}  {lat:.6f}, {lon:.6f}   {names}{via}")
        else:
            print(f"  no junction of '{args.lookup[0]}' and '{args.lookup[1]}'")
            for s in args.lookup:
                near = suggest(args.db, s)
                print(f"    '{s}' -> {near if near else 'not in extract'}")
        return 0

    if args.streets:
        streets = [s.strip() for s in args.streets.split(",") if s.strip()]
        ok = 0
        for a, b in zip(streets, streets[1:]):
            rows = lookup(args.db, a, b)
            if rows:
                ok += 1
                nid, lat, lon, kind, names = rows[0]
                via = " (ramp)" if kind == "ramp" else ""
                extra = f"  +{len(rows)-1} more" if len(rows) > 1 else ""
                print(f"  {a} x {b}: {lat:.6f}, {lon:.6f}  node {nid}{via}{extra}")
            else:
                print(f"  {a} x {b}: NO JUNCTION")
        print(f"\n  {ok}/{len(streets)-1} consecutive pairs intersect")
        return 0

    if not args.pbf:
        ap.error("provide a .pbf to build, or --lookup / --streets to query")
    return build(args.pbf, args.db, keep_service=not args.no_service,
                 ramps=not args.no_ramps)


if __name__ == "__main__":
    sys.exit(main())