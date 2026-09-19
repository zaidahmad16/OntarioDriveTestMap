#!/usr/bin/env python3
"""
manual_routes.py — build route_lines directly from the user's own manual
YouTube-video route transcriptions (Downloads/Manual Route trace youtube.md),
instead of inferring routes from crowdsourced traces.

DESTINATION: manual_routes.py (repo root)

This is ground truth, hand-verified by the user watching real drive-test
videos street by street -- strictly better than anything rebuild_routes.py
inferred from trace data. It also reveals the real route counts are
DIFFERENT from what was previously assumed (ROUTE_COUNTS in
rebuild_routes.py): Walkley G=3 (not 1), Canotek G=2, Canotek G2=3.

Each route below is transcribed as an ordered list of street names (the
same "turns" shape used elsewhere in the pipeline), consecutive duplicates
collapsed, parking/turn maneuvers with no street change dropped. The centre
coordinate is prepended/appended so every route starts and ends there.

Every consecutive street pair is resolved against data/osm.db via the same
Graph.junction() used by consensus_geometry.py -- no invented streets, no
disambiguation beyond what's already used elsewhere in the pipeline
(first real node, node before ramp). Pairs that fail to resolve are
reported, not guessed.

Safety: dry-run by default, writes GeoJSON to data/out/manual/ for
inspection. --apply backs up every route_lines/route_line_steps/
route_line_segments row first (same pattern as rebuild_routes.py), then
DELETEs every row with source='rebuild_routes' (the previous, wrong-
route-count data this supersedes) and INSERTs these 13. This REPLACES,
not merges -- do not run --apply until the two flagged uncertain
substitutions (see ROUTES comments: Smiths Falls "Regional Rd", Canotek
G route1 "27"->417) have had a human look, ideally against the source
video.
"""
import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis"))
import consensus_geometry as cg  # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "osm.db")
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "out", "manual")

CENTRE_COORDS = {
    "walkley": (45.376145807017544, -75.64758859649123),
    "canotek": (45.4528876, -75.5883806),
    "smithsfalls": (44.8827581, -76.0150536),
}

# centre, class, family index -> ordered street sequence (consecutive dups
# already collapsed by hand from the transcript). Ambiguous / suspect names
# flagged inline with a comment -- these are the ones to triple-check.
ROUTES = {
    ("smithsfalls", "G2", 0): [
        # "Tulon"->Toulon, "Lavina"->Lavinia, "Andrew"->Andrews: confirmed
        # against real OSM names (see manual_routes_report.md).
        "Percy St", "Toulon St", "Brockville St", "Davidson St W",
        "Lavinia St", "St. Lawrence St", "Andrews Ave", "Broadview Ave W",
        "Brockville St", "Van Horne Ave", "Percy St",
    ],
    ("smithsfalls", "G", 0): [
        # "Regional Rd" (x2) -> County Road 29: no street named "Regional
        # Rd" exists in this OSM extract near Smiths Falls; County Road 29
        # is the only road touching BOTH Eric Hutcheson Rd and Brockville
        # St at the right spot. Confidence: medium -- flagged for review.
        "Van Horne Ave", "Brockville St", "County Road 29", "Brockville St",
        "County Road 29", "Eric Hutcheson Rd", "County Road 29",
        "Brockville St", "Jasper Ave", "Brockville St", "Jasper Ave",
        "Old Slys Rd", "Jasper Ave", "Beckwith St", "Chambers St",
        "Market St", "Main St", "Beckwith St", "Brockville St",
        "County Road 29", "Brockville St", "County Road 29", "Brockville St",
        "Broadview Ave", "Percy St",
    ],
    ("canotek", "G2", 0): [
        "Canotek Rd", "Shefford Rd", "Montreal Rd", "Ogilvie Rd",
        "Appleford St", "Elmridge Dr", "Appleford St", "Crownhill St",
        "Seguin St", "Blair Rd", "Ogilvie Rd", "Montreal Rd", "Shefford Rd",
        "Canotek Rd",
    ],
    ("canotek", "G2", 1): [
        "Canotek Rd", "Shefford Rd", "Montreal Rd",
        # "Ottawa 34" dropped -- almost certainly Montreal Rd's route
        # number (Regional Rd 34), not a distinct street. TRIPLE-CHECK.
        "Miss Ottawa St", "Lerner Way", "Miss Ottawa St", "East Acres Rd",
        "Shefford Rd",
    ],
    ("canotek", "G2", 2): [
        "Loyola Ave", "Eastvale Dr", "Grafton Crescent", "Eastvale Dr",
        "Ogilvie Rd", "Montreal Rd", "Shefford Rd",
    ],
    ("canotek", "G", 0): [
        "Canotek Rd", "Shefford Rd", "Montreal Rd",
        # highway leg -- this is the segment previous sessions found was
        # NEVER collected in any crowdsourced trace. TRIPLE-CHECK every
        # name below against the real drive, Google narration is
        # unreliable here.
        # "Queensway" as transcribed matches a RURAL road near Smiths
        # Falls in this OSM extract, not Highway 417 -- dropped rather
        # than routed through the wrong province. OSRM bridges the real
        # gap between St Joseph Blvd and Montreal Rd on its own.
        "Regional Rd 174", "Jeanne-d'Arc Blvd", "Youville Dr",
        "St Joseph Blvd", "Jeanne-d'Arc Blvd", "Montreal Rd", "Shefford Rd",
    ],
    ("canotek", "G", 1): [
        "Shefford Rd", "Montreal Rd", "Ogilvie Rd",
        # "27" as transcribed -- Highway 27 is nowhere near Canotek (west
        # end of Ottawa, near the airport). Near Blair/Ogilvie the real
        # highway is 417. SUSPECT MISTRANSCRIPTION, TRIPLE-CHECK.
        "Highway 417", "Montreal Rd", "Shefford Rd",
    ],
    ("walkley", "G2", 0): [
        "Walkley Rd", "Heatherington Rd", "Fairlea Cres", "Heatherington Rd",
        "Walkley Rd", "Baycrest Dr", "Cedarwood Dr", "Walkley Rd",
    ],
    ("walkley", "G2", 1): [
        "Walkley Rd", "Cedarwood Dr", "Baycrest Dr", "Heron Rd",
        "Briar Hill Dr", "Featherston Dr", "Jefferson St", "Heron Rd",
        "Walkley Rd",
    ],
    ("walkley", "G2", 2): [
        "Walkley Rd", "Baycrest Dr", "Heron Rd", "Briar Hill Dr",
        "Featherston Dr", "Jefferson St", "Heron Rd", "Baycrest Dr",
        "Cedarwood Dr", "Walkley Rd",
    ],
    ("walkley", "G", 0): [
        # transcript is TRUNCATED after this point (no return-to-centre
        # text) -- route completed by repeating the last street back to
        # the centre, not by inventing new streets. FLAG, don't trust
        # blindly.
        "Walkley Rd", "Airport Parkway", "Uplands Dr", "Airport Parkway",
        "Walkley Rd",
    ],
    ("walkley", "G", 1): [
        "Walkley Rd", "Airport Parkway", "Hunt Club Rd", "Uplands Dr",
        "Paul Anka Dr", "McCarthy Rd", "Hunt Club Rd", "Airport Parkway",
        "Walkley Rd",
    ],
    ("walkley", "G", 2): [
        "Walkley Rd", "Heatherington Rd", "Albion Rd N", "Kitchener Ave",
        "Banff Ave", "St Paul Ave", "Bank St", "Hunt Club Rd W",
        "Airport Parkway", "Walkley Rd",
    ],
}


import unicodedata
import re

_EXPAND = {
    "rd": "road", "st": "street", "ave": "avenue", "dr": "drive",
    "blvd": "boulevard", "pkwy": "parkway", "cres": "crescent",
    "crt": "court", "ct": "court", "ln": "lane", "pl": "place",
}
_SUFFIXES = set(_EXPAND.values())


def _norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    words = re.sub(r"[^\w\s]", " ", s.lower()).split()
    return " ".join(_EXPAND.get(w, w) for w in words)


def _base(s):
    w = _norm(s).split()
    while w and w[-1] in _SUFFIXES:
        w.pop()
    return " ".join(w)


SPUR_PATH = os.path.join(OUT_DIR, "spur_nodes.json")
_SPUR_RAW = json.load(open(SPUR_PATH)) if os.path.exists(SPUR_PATH) else {}
# re-key by suffix-stripped base ("Cedarwood Dr" and "cedarwood drive"
# both -> "cedarwood") so abbreviation differences between the transcript
# and OSM's full-word tags don't cause a silent miss.
_SPUR = {}
for k, v in _SPUR_RAW.items():
    bk = k if k.startswith("ref:") else _base(k)
    _SPUR.setdefault(bk, []).extend(v)


# Streets that are either short spurs (a parking-maneuver detour off a
# through street: Elmridge, Lerner, Grafton) or streets whose two flanking
# junctions can be satisfied by OSRM's shortest path WITHOUT ever actually
# turning onto them, or ways with no name in OSM at all (Highway 417 --
# ref-tagged, never gets a junction entry). Verified by checking each
# route's actual OSRM step names against the transcript and finding these
# silently absent (see manual_routes_report.md). A real point sampled from
# the way's own geometry, inserted as an explicit waypoint, forces OSRM to
# actually drive the street instead of shortcutting past it.
SPUR_KEYS = {
    "cedarwood drive", "uplands drive", "elmridge drive", "lerner way",
    "grafton crescent", "regional road 174", "youville drive",
    "ogilvie road", "eric hutcheson road", "old slys road",
}
REF_KEYS = {"417": "ref:417"}


def spur_point(name, near_lat, near_lon):
    """Nearest real node, among all OSM instances of `name` province-wide,
    to (near_lat, near_lon). None if the name isn't a known spur or
    nothing is within MAX_JUMP_M."""
    n = _base(name)
    ref_match = next((rk for num, rk in REF_KEYS.items()
                       if num in _norm(name).split()), None)
    jkey = n if n in _SPUR else ref_match
    if not jkey or jkey not in _SPUR:
        return None
    best, best_d = None, None
    for way in _SPUR[jkey]:
        for lat, lon in way["coords"]:
            d = cg.haversine((near_lat, near_lon), (lat, lon))
            if best_d is None or d < best_d:
                best, best_d = (lat, lon), d
    if best is None or best_d > MAX_JUMP_M:
        return None
    return best


# osm.db is built from the WHOLE Ontario extract, not just each centre's
# neighbourhood. A plain first-match junction lookup (what Graph.junction
# does, fine for trace clustering where streets are already local) can
# grab a same-named street on the other side of the province -- this is
# what blew Smiths Falls G out to 170km (a "Jasper Avenue"/"Brockville
# Street" match nowhere near Smiths Falls). Every candidate junction is
# fetched and the one nearest the current position on the route wins;
# anything implausibly far (>MAX_JUMP_M) is treated as unresolved rather
# than silently teleporting the route.
MAX_JUMP_M = 15_000


def best_junction(g, a, b, near_lat, near_lon):
    va, vb = cg.name_variants(a), cg.name_variants(b)
    pa = ",".join("?" * len(va))
    pb = ",".join("?" * len(vb))
    rows = g.con.execute(f"""
        SELECT j.node_id, j.lat, j.lon, j.kind FROM junctions j
        WHERE j.node_id IN (SELECT node_id FROM junction_streets
                            WHERE base IN ({pa}) OR full IN ({pa}))
          AND j.node_id IN (SELECT node_id FROM junction_streets
                            WHERE base IN ({pb}) OR full IN ({pb}))
    """, (*va, *va, *vb, *vb)).fetchall()
    if not rows:
        return None
    best, best_d = None, None
    for r in rows:
        d = cg.haversine((near_lat, near_lon), (r["lat"], r["lon"]))
        if best_d is None or d < best_d:
            best, best_d = dict(r), d
    if best_d > MAX_JUMP_M:
        return None
    return best


def _add(pts, last_key, lat, lon):
    key = (round(lat, 6), round(lon, 6))
    if key != last_key:
        pts.append({"lat": lat, "lon": lon})
        last_key = key
    return last_key


def resolve(centre, streets, g, pause=0.3):
    centre_ll = CENTRE_COORDS[centre]
    pts = [{"lat": centre_ll[0], "lon": centre_ll[1]}]
    failed = []
    forced_spurs = []
    last_key = None
    cur_lat, cur_lon = centre_ll
    for a, b in zip(streets, streets[1:]):
        # 'a' is the street currently being driven on the leg into this
        # junction -- if it's a known spur/shortcut-prone street, force a
        # real point on it BEFORE the junction so OSRM can't bypass it.
        sp = spur_point(a, cur_lat, cur_lon)
        if sp:
            last_key = _add(pts, last_key, sp[0], sp[1])
            cur_lat, cur_lon = sp
            forced_spurs.append(a)

        j = best_junction(g, a, b, cur_lat, cur_lon)
        if not j:
            failed.append((a, b))
            continue
        cur_lat, cur_lon = j["lat"], j["lon"]
        last_key = _add(pts, last_key, j["lat"], j["lon"])

    # the final street ('streets[-1]') is never the 'a' of a pair -- check
    # it too, since it's the leg running back into the centre.
    sp = spur_point(streets[-1], cur_lat, cur_lon)
    if sp:
        last_key = _add(pts, last_key, sp[0], sp[1])
        forced_spurs.append(streets[-1])

    pts.append({"lat": centre_ll[0], "lon": centre_ll[1]})
    return pts, failed, forced_spurs


def main():
    cg.load_known(DB_PATH)
    g = cg.Graph(DB_PATH)
    os.makedirs(OUT_DIR, exist_ok=True)

    by_centre = {}
    all_failed = {}
    for (centre, cls, idx), streets in ROUTES.items():
        pts, failed, forced_spurs = resolve(centre, streets, g)
        n_resolved_pairs = len(streets) - 1 - len(failed)
        print(f"=== {centre} {cls} route{idx} === "
              f"{n_resolved_pairs}/{len(streets)-1} pairs resolved, "
              f"{len(pts)} waypoints"
              + (f", forced spurs: {forced_spurs}" if forced_spurs else ""))
        if failed:
            all_failed[(centre, cls, idx)] = failed
            for a, b in failed:
                print(f"    NO JUNCTION: '{a}' <-> '{b}'")

        geom, dist, steps = None, None, []
        try:
            r = cg.osrm_route(pts, pause=0.3)
            if r.get("code") == "Ok":
                geom = r["routes"][0]["geometry"]
                dist = r["routes"][0]["distance"]
                steps = cg.extract_steps(r, g)
            else:
                print(f"    OSRM: {r.get('code')} {r.get('message','')}")
        except Exception as e:
            print(f"    OSRM error: {str(e)[:100]}")
        if geom is None:
            geom = {"type": "LineString",
                    "coordinates": [[p["lon"], p["lat"]] for p in pts]}

        by_centre.setdefault(centre, []).append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "test_class": cls, "family": idx, "run": 0,
                "distance_m": round(dist) if dist else None,
                "steps": steps, "street_sequence": streets,
                "failed_pairs": failed, "forced_spurs": forced_spurs,
                "source": "manual_youtube",
            },
        })

    for centre, feats in by_centre.items():
        out = os.path.join(OUT_DIR, f"{centre}.geojson")
        json.dump({"type": "FeatureCollection", "features": feats},
                  open(out, "w"), indent=1)
        print(f"wrote {out} ({len(feats)} routes)")

    if all_failed:
        print(f"\n{len(all_failed)} route(s) have unresolved street pairs -- "
              "see NO JUNCTION lines above.")

    if args.apply:
        apply_to_db(by_centre)
    else:
        print("\nDry-run only, DB untouched. Add --apply to write "
              "(replaces every rebuild_routes-sourced row).")


DATABASE_URL = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")


def apply_to_db(by_centre):
    """Same backup-then-replace pattern as rebuild_routes.py. This
    REPLACES rebuild_routes-sourced rows (the old, wrong route counts),
    it does not merge with them -- manual_youtube is the new source of
    truth for every centre it covers."""
    import psycopg2
    from psycopg2.extras import RealDictCursor
    if not DATABASE_URL:
        sys.exit("Set DATABASE_PUBLIC_URL (or DATABASE_URL) before --apply.")
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    backup = {}
    for t in ("route_lines", "route_line_steps", "route_line_segments"):
        cur.execute(f"SELECT * FROM {t}")
        backup[t] = [dict(r) for r in cur.fetchall()]
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    bpath = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         f"route_data_backup_manual_{ts}.json")
    json.dump(backup, open(bpath, "w"), default=str, indent=1)
    print(f"backup written: {bpath} "
          f"({len(backup['route_lines'])} route_lines, "
          f"{len(backup['route_line_steps'])} steps)")

    for centre, feats in by_centre.items():
        cur.execute(
            "DELETE FROM route_lines WHERE centre_id = %s AND source = %s",
            (centre, "rebuild_routes"))
        for feat in feats:
            p = feat["properties"]
            coords = feat["geometry"]["coordinates"]
            cur.execute(
                """
                INSERT INTO route_lines
                    (centre_id, family, run, trace_count, authors, distance_m,
                     geometry, test_class, mixed_classes, below_threshold,
                     predicted, source)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
                """,
                (centre, p["family"], p["run"], 1, 1, p["distance_m"],
                 json.dumps(coords), p["test_class"], False, False, False,
                 "manual_youtube"),
            )
            rid = cur.fetchone()["id"]
            cg.insert_steps(cur, rid, p["steps"])
        print(f"   {centre}: wrote {len(feats)} routes")

    conn.commit()
    cur.close()
    conn.close()
    print("committed.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write to the live DB (replaces rebuild_routes rows)")
    args = ap.parse_args()
    main()
