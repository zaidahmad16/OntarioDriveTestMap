#!/usr/bin/env python3
"""
build_intersection_index.py — turn an OSM extract into an offline
intersection lookup and street gazetteer.

Replaces geocoding entirely. An intersection is a node shared by two or
more named drivable ways, which is a fact in the extract rather than
something a geocoder has to be persuaded to infer.

Solves four problems with one artifact:
  1. Intersection lookup: "cedarwood" + "walkley" -> coordinate, or a
     definite "these streets do not meet", which is a trace validation
     signal rather than a geocoding failure
  2. Gazetteer: every street name in the region, so extraction stops
     missing streets it was never told about
  3. Node-to-way mapping: OSRM annotates routes with node IDs, not way
     IDs, so this is what makes the way-ID representation possible
  4. Offline and unrated: no public service in the hot path
"""

import argparse
import os
import re
import sqlite3
import sys
from collections import defaultdict

# Drivable highway types. Footways and cycleways would create junctions
# a car cannot use, which would then look like valid route options.
DRIVABLE = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential",
    "living_street", "service", "road",
}

SUFFIXES = {
    "road", "rd", "street", "st", "avenue", "ave", "drive", "dr",
    "boulevard", "blvd", "parkway", "pkwy", "crescent", "cres",
    "court", "crt", "ct", "lane", "ln", "way", "place", "pl",
    "terrace", "terr", "circle", "cir", "trail", "private",
    "north", "south", "east", "west", "n", "s", "e", "w",
}


def normalise(name):
    """'Cedarwood Drive' -> 'cedarwood'.

    Reddit and transcripts give bare names. The extract gives full ones.
    Normalising both to a base form is what lets them meet.
    """
    s = re.sub(r"[^\w\s]", " ", (name or "").lower())
    words = [w for w in s.split() if w]
    while words and words[-1] in SUFFIXES:
        words.pop()
    return " ".join(words) if words else (name or "").lower().strip()


def full_form(name):
    """Normalised but with the suffix intact.

    Stripping is right for 'Bank Street' -> 'bank', which is how people
    write it, and wrong for 'Airport Parkway' -> 'airport', where the
    suffix is part of the name everyone uses. Rather than guessing which
    streets are special, index both forms and match on either.
    """
    s = re.sub(r"[^\w\s]", " ", (name or "").lower())
    return " ".join(s.split())


def variants(name):
    """Both forms of a query string, deduped."""
    return list({normalise(name), full_form(name)} - {""})


SCHEMA = """
CREATE TABLE IF NOT EXISTS streets (
    name       TEXT NOT NULL,
    base       TEXT NOT NULL,
    full       TEXT NOT NULL,
    way_id     INTEGER NOT NULL,
    highway    TEXT
);
CREATE TABLE IF NOT EXISTS junctions (
    node_id    INTEGER PRIMARY KEY,
    lat        REAL NOT NULL,
    lon        REAL NOT NULL,
    n_streets  INTEGER NOT NULL
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


def build(pbf_path, db_path, keep_service=True):
    try:
        import osmium
    except ImportError:
        print("pyosmium not installed.  pip install osmium")
        return 1

    drivable = set(DRIVABLE)
    if not keep_service:
        drivable.discard("service")

    node_streets = defaultdict(set)
    way_rows = []

    class WayPass(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.n = 0

        def way(self, w):
            hw = w.tags.get("highway")
            if hw not in drivable:
                return
            name = w.tags.get("name")
            if not name:
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
    print(f"  nodes touched:       {len(node_streets):,}")

    # A junction is a node where two or more DISTINCT street names meet.
    # Comparing names not way IDs, since one street is usually split
    # across many ways and every split point would otherwise look like
    # an intersection.
    junction_ids = {
        nid for nid, pairs in node_streets.items()
        if len({name for name, _ in pairs}) >= 2
    }
    print(f"  junction nodes:      {len(junction_ids):,}")

    coords = {}

    class NodePass(osmium.SimpleHandler):
        def node(self, n):
            if n.id in junction_ids and n.location.valid():
                coords[n.id] = (n.location.lat, n.location.lon)

    print("pass 2: reading node coordinates")
    NodePass().apply_file(pbf_path)
    print(f"  located:             {len(coords):,}")

    missing = len(junction_ids) - len(coords)
    if missing:
        print(f"  ! {missing:,} junctions had no coordinate "
              f"(nodes outside the extract boundary)")

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
        jrows.append((nid, lat, lon, len({n for n, _ in pairs})))
        for name, wid in pairs:
            jsrows.append((nid, name, normalise(name),
                           full_form(name), wid))

    con.executemany(
        "INSERT INTO junctions (node_id, lat, lon, n_streets) VALUES (?,?,?,?)",
        jrows)
    con.executemany(
        "INSERT INTO junction_streets (node_id, name, base, full, way_id) "
        "VALUES (?,?,?,?,?)", jsrows)
    con.commit()

    n_streets = con.execute(
        "SELECT COUNT(DISTINCT base) FROM streets").fetchone()[0]
    print(f"\nwrote {db_path}")
    print(f"  distinct street names: {n_streets:,}")
    print(f"  junctions:             {len(jrows):,}")
    con.close()
    return 0


def lookup(db_path, a, b):
    """Find junctions where streets a and b meet.

    Matches a query against either the stripped base or the full form,
    so 'bank' finds Bank Street and 'airport parkway' finds Airport
    Parkway. Parameterised throughout; placeholders are generated from
    the variant count, never from the values.
    """
    va, vb = variants(a), variants(b)
    pa = ",".join("?" * len(va))
    pb = ",".join("?" * len(vb))
    con = sqlite3.connect(db_path)
    rows = con.execute(f"""
        SELECT j.node_id, j.lat, j.lon,
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
    """Nearest known street names, for when a lookup misses."""
    con = sqlite3.connect(db_path)
    base = normalise(name)
    rows = con.execute(
        "SELECT DISTINCT base FROM streets WHERE base LIKE ? LIMIT ?",
        (f"%{base}%", limit)).fetchall()
    if not rows:
        import difflib
        allb = [r[0] for r in con.execute(
            "SELECT DISTINCT base FROM streets").fetchall()]
        rows = [(m,) for m in difflib.get_close_matches(base, allb,
                                                        n=limit, cutoff=0.7)]
    con.close()
    return [r[0] for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pbf", nargs="?", help="path to .osm.pbf extract")
    ap.add_argument("--db", default="osm.db")
    ap.add_argument("--no-service", action="store_true",
                    help="exclude service roads (parking lots, driveways)")
    ap.add_argument("--lookup", nargs=2, metavar=("A", "B"))
    ap.add_argument("--streets", help="comma separated ordered trace")
    args = ap.parse_args()

    if args.lookup:
        rows = lookup(args.db, *args.lookup)
        if rows:
            for nid, lat, lon, names in rows:
                print(f"  node {nid}  {lat:.6f}, {lon:.6f}   {names}")
        else:
            print(f"  no junction of '{args.lookup[0]}' and "
                  f"'{args.lookup[1]}'")
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
                nid, lat, lon, names = rows[0]
                extra = f"  (+{len(rows)-1} more)" if len(rows) > 1 else ""
                print(f"  {a} x {b}: {lat:.6f}, {lon:.6f}  node {nid}{extra}")
            else:
                print(f"  {a} x {b}: NO JUNCTION")
        print(f"\n  {ok}/{len(streets)-1} consecutive pairs intersect")
        if ok < len(streets) - 1:
            print("  A missing pair means the trace skips a connecting "
                  "street, or a name did not match the extract.")
        return 0

    if not args.pbf:
        ap.error("provide a .pbf to build, or --lookup / --streets to query")
    return build(args.pbf, args.db, keep_service=not args.no_service)


if __name__ == "__main__":
    sys.exit(main())
