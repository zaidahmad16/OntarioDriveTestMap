#!/usr/bin/env python3
"""
validate_geometry.py — does the street-names-to-polyline path work at all?

Twenty-minute validation of the riskiest untested step. Takes an ordered
list of streets, converts consecutive pairs into intersections, geocodes
them, feeds the coordinates to OSRM, and reports whether a road-snapped
route comes back.

Deliberately not the pipeline. No caching, no retries beyond a token one,
no database. If this works, the real version uses a local OSM extract for
intersections and a local OSRM in Docker.

Both services here are free public instances run on donated capacity:
  - Nominatim asks for <=1 req/sec and a real User-Agent
  - The OSRM demo server is explicitly not for production use
So this stays at a handful of requests. Move to local before any real run.
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OSRM = "https://router.project-osrm.org/route/v1/driving"

UA = "OntarioRoadTestMap/0.1 (validation script; ontariodrivetestmap.fyi)"

# Ottawa-ish. Anything outside this is a bad geocode, not a real result.
# Nominatim will happily return a Walkley Road in the UK.
BBOX = {"min_lat": 44.9, "max_lat": 45.6, "min_lon": -76.4, "max_lon": -75.2}

# From record jt7gwng, a 2023 Walkley G2 account. A closed loop, so a
# wrong result is visually obvious rather than plausible-looking.
DEFAULT_STREETS = ["walkley", "cedarwood", "baycrest", "walkley"]

CITY = "Ottawa, Ontario, Canada"


def get(url, params):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{q}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def in_bbox(lat, lon):
    return (BBOX["min_lat"] <= lat <= BBOX["max_lat"]
            and BBOX["min_lon"] <= lon <= BBOX["max_lon"])


def geocode_intersection(a, b, pause=1.1):
    """Resolve 'A & B' to a coordinate.

    Nominatim has no real intersection endpoint. The '&' form works
    sometimes; 'A and B' works other times. Try both, take the first
    hit inside the bounding box.
    """
    forms = [f"{a} & {b}, {CITY}", f"{a} and {b}, {CITY}"]
    for form in forms:
        try:
            res = get(NOMINATIM, {
                "q": form, "format": "json", "limit": 3,
                "countrycodes": "ca",
            })
        except Exception as e:
            print(f"    ! request failed: {e}")
            time.sleep(pause)
            continue

        for hit in res:
            lat, lon = float(hit["lat"]), float(hit["lon"])
            if in_bbox(lat, lon):
                time.sleep(pause)
                return {
                    "query": form, "lat": lat, "lon": lon,
                    "display_name": hit.get("display_name", ""),
                    "osm_type": hit.get("osm_type"),
                    "osm_id": hit.get("osm_id"),
                }
            print(f"    - out of bbox: {hit.get('display_name','')[:70]}")
        time.sleep(pause)
    return None


def route(points):
    """OSRM route through the given coordinates.

    annotations=nodes gives the OSM node IDs the route passes through.
    OSRM does not expose way IDs, so the design doc's 'sequence of OSM
    way IDs' representation needs either a node->way mapping from the
    extract, or a switch to node sequences.
    """
    coords = ";".join(f"{p['lon']},{p['lat']}" for p in points)
    return get(f"{OSRM}/{coords}", {
        "overview": "full",
        "geometries": "geojson",
        "annotations": "nodes",
        "steps": "true",
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--streets", default=",".join(DEFAULT_STREETS),
                    help="ordered street names, comma separated")
    ap.add_argument("--geojson", default="trace.geojson")
    ap.add_argument("--pause", type=float, default=1.1)
    args = ap.parse_args()

    streets = [s.strip() for s in args.streets.split(",") if s.strip()]
    if len(streets) < 2:
        print("need at least two streets")
        return 1

    pairs = list(zip(streets, streets[1:]))
    print(f"Trace: {' -> '.join(streets)}")
    print(f"{len(pairs)} intersections to resolve\n")

    print("== 1. geocoding ==")
    points, failed = [], []
    for a, b in pairs:
        print(f"  {a} & {b}")
        hit = geocode_intersection(a, b, args.pause)
        if hit:
            print(f"    ok  {hit['lat']:.5f}, {hit['lon']:.5f}")
            print(f"        {hit['display_name'][:80]}")
            points.append(hit)
        else:
            print("    FAILED")
            failed.append(f"{a} & {b}")

    print(f"\n  resolved {len(points)}/{len(pairs)}")
    if failed:
        print(f"  failed: {', '.join(failed)}")

    if len(points) < 2:
        print("\nNot enough points to route. The geocoding step is the "
              "blocker, which points straight at building the intersection "
              "index from a local extract.")
        return 1

    print("\n== 2. routing ==")
    try:
        r = route(points)
    except Exception as e:
        print(f"  OSRM request failed: {e}")
        return 1

    if r.get("code") != "Ok":
        print(f"  OSRM returned: {r.get('code')} {r.get('message','')}")
        return 1

    leg_route = r["routes"][0]
    geom = leg_route["geometry"]["coordinates"]
    nodes = []
    for leg in leg_route.get("legs", []):
        ann = leg.get("annotation", {})
        nodes.extend(ann.get("nodes", []))

    print("  ok")
    print(f"  distance   {leg_route['distance']/1000:.2f} km")
    print(f"  duration   {leg_route['duration']/60:.1f} min")
    print(f"  geometry   {len(geom)} coordinate pairs")
    print(f"  osm nodes  {len(nodes)} ({len(set(nodes))} distinct)")

    print("\n== 3. sanity ==")
    d = leg_route["distance"] / 1000
    if d < 0.5:
        print("  ! under 500 m. Geocodes probably collapsed to one spot.")
    elif d > 25:
        print("  ! over 25 km. A geocode likely landed on the wrong street.")
    else:
        print(f"  distance plausible for a test route leg ({d:.2f} km)")

    if streets[0] == streets[-1]:
        start, end = geom[0], geom[-1]
        gap = ((start[0]-end[0])**2 + (start[1]-end[1])**2) ** 0.5
        print(f"  closed loop expected; start-end gap {gap*111:.2f} km approx")

    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "properties": {"kind": "route",
                            "streets": streets,
                            "distance_km": round(d, 3),
                            "node_count": len(nodes)},
             "geometry": leg_route["geometry"]},
        ] + [
            {"type": "Feature",
             "properties": {"kind": "intersection",
                            "query": p["query"],
                            "name": p["display_name"]},
             "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]}}
            for p in points
        ],
    }
    with open(args.geojson, "w") as f:
        json.dump(fc, f, indent=2)

    print(f"\nWrote {args.geojson}")
    print("Drag it onto geojson.io to see whether the line follows real "
          "roads and whether the intersections landed where they should.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
