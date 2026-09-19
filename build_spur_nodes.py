#!/usr/bin/env python3
"""
build_spur_nodes.py — real node geometry for streets manual_routes.py
can't trust a single junction coordinate for.

DESTINATION: build_spur_nodes.py (repo root)

Some named streets in a manually-transcribed route are short spurs used
only for a parking maneuver (Elmridge, Lerner, Grafton), or a street
whose two flanking junctions can be satisfied by OSRM's shortest path
WITHOUT the route ever actually turning onto them, or -- worst case --
a ref-tagged highway with no name in OSM at all (417, never gets a
junction_streets entry). A single junction coordinate can't force OSRM
onto these; a real point sampled from partway down the street's own
geometry can. Requires pyosmium (venv/bin/python3) and
data/raw/ontario-latest.osm.pbf. Re-run this if TARGETS/TARGET_REFS in
manual_routes.py's spur list grows -- output feeds manual_routes.py via
data/out/manual/spur_nodes.json."""
import json
import sys
import unicodedata
import re

import osmium

DRIVABLE = {
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential",
    "living_street", "service", "road",
}

def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", s.lower()).split())

TARGETS = {
    "cedarwood drive", "uplands drive", "elmridge drive", "lerner way",
    "grafton crescent", "regional road 174", "youville drive",
    "ogilvie road", "eric hutcheson road", "old slys road",
}
TARGET_REFS = {"417"}

near_bound = {  # generous boxes: (min_lat,max_lat,min_lon,max_lon)
    "ottawa": (45.15, 45.55, -75.85, -75.35),
    "smithsfalls": (44.80, 44.98, -76.10, -75.95),
}

def in_bounds(lat, lon):
    for b in near_bound.values():
        if b[0] <= lat <= b[1] and b[2] <= lon <= b[3]:
            return True
    return False

matches = {}  # name -> list of {way_id, coords:[[lat,lon],...]}

class H(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.n = 0

    def way(self, w):
        hw = w.tags.get("highway")
        if hw not in DRIVABLE:
            return
        name = w.tags.get("name")
        ref = w.tags.get("ref")
        key = None
        if name and norm(name) in TARGETS:
            key = norm(name)
        elif ref and ref in TARGET_REFS:
            key = f"ref:{ref}"
        if not key:
            return
        pts = [(n.lat, n.lon) for n in w.nodes if n.location.valid()]
        if not pts or not in_bounds(*pts[len(pts)//2]):
            return
        self.n += 1
        matches.setdefault(key, []).append({
            "way_id": w.id, "coords": pts,
        })

print("scanning ontario-latest.osm.pbf ...", file=sys.stderr)
h = H()
h.apply_file("data/raw/ontario-latest.osm.pbf", locations=True)
print(f"done, {h.n} matching ways", file=sys.stderr)
json.dump(matches, open("data/out/manual/spur_nodes.json", "w"))
for k, v in matches.items():
    print(k, len(v), "ways")
