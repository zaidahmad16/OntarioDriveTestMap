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
        # "Regional Rd" (x2, after Eric Hutcheson) -> Jasper Road:
        # owner confirmed from the video ("jasper rd then jasper ave").
        # Real OSM junction node 389198623 is literally "Eric Hutcheson
        # Road, Jasper Avenue, Jasper Road" all meeting at one point --
        # Jasper Road becomes Jasper Avenue right there. High confidence,
        # owner-confirmed.
        # (Earlier "County Rd" near Van Horne/Brockville is unrelated --
        # that's really County Road 29, unaffected by this correction.)
        "Van Horne Ave", "Brockville St", "County Road 29", "Brockville St",
        "County Road 29", "Eric Hutcheson Rd", "Jasper Road", "Jasper Ave",
        "Jasper Road", "Old Slys Rd", "Jasper Ave", "Beckwith St",
        "Chambers St", "Market St", "Main St", "Beckwith St", "Brockville St",
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

# (a, b, [(lat,lon), ...]): pins a specific occurrence of a street pair
# to real coordinates instead of trusting "nearest candidate to current
# position" -- for pairs with more than one real junction (a crescent
# looping off a road has two), nearest isn't always the one actually
# driven. All points are visited in order; only the LAST is treated as
# "arrived at this junction" for later overrides/spurs to build from.
# Each entry is consumed once, in order, so a pair appearing twice in
# one route can have two different overrides.
JUNCTION_OVERRIDES = {
    ("walkley", "G2", 0): [
        # owner, from the video: "at the 2nd stop sign [not the 1st],
        # turn left onto Fairlea Cres" -- Heatherington Rd x Fairlea
        # Crescent has 2 real junctions ~200m apart (it loops off
        # Heatherington and back on); the nearer one (~125m from the
        # Walkley/Heatherington turn) is the 1st, the farther one
        # (~320m) is the 2nd. Owner confirmed farther is correct.
        ("Heatherington Rd", "Fairlea Cres", [(45.3752609, -75.6435828)]),
        # The EXIT pair ("Fairlea Cres" -> "Heatherington Rd") needs to
        # reach the OTHER real Heatherington junction (903993646, the
        # "1st stop sign" node) -- but forcing just that endpoint wasn't
        # enough: OSRM's shortest path between the two endpoints cut
        # through Angela Private (a real, shorter road) instead of
        # actually driving the crescent, confirmed by the owner from the
        # rendered map. A real midpoint node from Fairlea Crescent's own
        # OSM way (76729132), on the far side of the loop away from
        # Angela Private, forces the actual shape before arriving.
        ("Fairlea Cres", "Heatherington Rd",
         [(45.3761284, -75.6408992), (45.3769754, -75.6443981)]),
    ],
}


# Owner's own words from the transcript, verbatim (numbering and pure
# "@Centre"/"@Cetnre" marker lines stripped, nothing else changed --
# including the owner's own spelling of street names). This is what
# actually gets shown in the app's turn-by-turn table now, in place of
# OSRM's generic auto-generated phrasing: the owner watched the real
# video and wrote these down, they're more trustworthy than a template.
# Real distance/duration only make sense at the whole-route level here
# (these lines don't map 1:1 onto OSRM's own routing legs) -- per-step
# distance/duration/junction/speed-limit are left blank, not guessed.
TRANSCRIPT = {
    ("smithsfalls", "G2", 0): [
        "Turn right after exiting your parking spot. (Note: Park in reverse when you reach the test centre)",
        "Turn left on to Percy st from the stop sign",
        "Do a parallel parking on Percy st",
        "Turn left on Tulon St",
        "Wait at the stop sign and turn Right on to Brockville st.",
        "Turn left on to davidson st W",
        "Stop at stop sign on davidson st",
        "Curb side parking on davidson st W uphill with the curb",
        "At the stop sign turn right on to Lavina St",
        "Perform three-point turn on Lavina ST",
        "At the stop sign turn right onto St. Lawrence",
        "Continue on Andrew Ave.",
        "At the stop sign turn left on Broadview Ave W",
        "At the light, use the filter to turn right on to Brockville st",
        "At the intersection, turn left on to Van Horne Ave",
        "Continue on to Percy St",
        "Pull into the DriveTest Centre at the intersection",
    ],
    ("smithsfalls", "G", 0): [
        "At  the stop sign turn right on Van Horne",
        "At the stop sign, turn left on to Brockville St",
        "Continue on County Rd",
        "Continue on Brockville St",
        "Continue on County Rd",
        "At the light turn left on to Eric Hutcheson Rd",
        "Continue on Eric Hutcheson Rd",
        "At the stop sign turn left on to Regional Rd",
        "Continue on Regional Rd",
        "Continue on Jasper Ave",
        "Continue on Regional Rd",
        "At the stop Sign turn left onto Old slys Rd",
        "Continue on Old slys Rd",
        "Continue on Jasper Ave",
        "At the light turn right onto Beckwith",
        "At the light turn right onto Chambers",
        "At the Red flashing light, turn left onto Market St",
        "At the stop sign, turn left onto Main St",
        "At the light, turn left onto Beckwith St",
        "Continue on Beckwith St",
        "Continue on Brockville",
        "Continue on County Rd",
        "Continue on Brockville",
        "Continue on County Rd",
        "At the light, turn left onto Broadview",
        "Continue on Broadview",
        "Turn left onto Percy St",
        "At the stop sign,  Continue straight on Percy St",
        "Pull into the DriveTest Centre at the intersection",
    ],
    ("canotek", "G2", 0): [
        "At the Stop sign at the @centre, turn right onto Canotek Rd",
        "At the light, turn left into shefford",
        "Continue shefford",
        "At the light, turn tight onto Montreal Rd",
        "At the 3rd light, turn left into Ogilvie Rd",
        "At the light, turn right into Appleford St",
        "Prepare to perform an emergency stop on Appleford St",
        "Prepare to turn right into Elmridge dr",
        "Prepare to perform Parallel Parking and three point turn",
        "At the stop sign turn right onto Appleford St",
        "At the stop sign turn right at the 1st cross street onto Crownhill St",
        "Continue onto Seguin St",
        "At the stop sign Perform a complete stop",
        "At the end of the road/stop sign, turn left onto Blair rd",
        "At the light, turn left onto Ogilvie Rd",
        "At the light, turn right onto Montreal Rd",
        "At the light, turn left onto Shefford Rd",
        "At the light, turn right onto Canotek Rd",
        "Pull into the DriveTest Centre at the intersection",
    ],
    ("canotek", "G2", 1): [
        "Turn right onto Canotek rd",
        "At the light, turn left on Shefford rd",
        "At the light, turn right on Montreal rd",
        "Continue on Montreal rd",
        "Continue on Ottawa 34",
        "At the light turn left onto Miss Ottawa St",
        "Turn left onto Lerner Way",
        "Perform a three point turn on Lerner Way",
        "At the stop sign, left onto Miss Ottawa St",
        "At the stop sign turn left onto E Acres Rd",
        "Turn left on to Shefford Rd",
        "At the light, keep going straight",
        "Pull into the DriveTest Centre at the intersection",
    ],
    ("canotek", "G2", 2): [
        "Go straight on to Loyola Ave",
        "At the stop sign, turn right onto Eastvale Dr",
        "Continue on Eastvale Dr",
        "Turn left onto Grafton Crescent",
        "On Grafton Crescent,  perform uphill or downhill park",
        "On Grafton Crescent,  perform a three point turn",
        "At the stop sign, turn right on to Eastvale Dr",
        "Continue on Eastvale Dr",
        "At the stop sign, turn right and Continue on Eastvale Dr",
        "At the light, turn left onto Ogilvie Rd",
        "Continue on Ogilvie Rd",
        "At the light, turn left onto Montreal Rd",
        "Continue on Montreal Rd",
        "Continue on Ottawa 34",
        "At the light, turn left onto Shefford Rd",
        "Continue on Shefford Rd",
        "Pull into the DriveTest Centre at the intersection",
    ],
    ("canotek", "G", 0): [
        "At the stop sign, turn right onto Canotek Rd",
        "Continue on Canotek Rd",
        "At the light, turn left onto Shefford Rd",
        "Continue on Shefford Rd",
        "At the light, turn left on Montreal Rd",
        "Continue on Montreal Rd",
        "Continue on Ottawa 34",
        "At the light, Continue on Ottawa 34",
        "At the 2nd light, turn right onto the highway  174 East ramp",
        "Continue on Regional Rd 174",
        "Exit off onto Boul Jeanne-d'Arc Blvd",
        "Stick to the right lane and go right onto 55",
        "At the light, turn right onto Youville Dr",
        "Continue on Youville Dr",
        "At the light, turn left onto St Joseph Blvd",
        "Continue on St Joseph Blvd",
        "At the roundabout, exit left onto Jeanne-d'Arc Blvd",
        "Continue on Jeanne-d'Arc Blvd",
        "At the first light, continue Straight",
        "At the second light, continue Straight",
        "At the third light, turn right onto the Queenways ramp",
        "Continue on the Queensways",
        "Exit off on Ch. de Montreal Rd.",
        "Keep to the right lane and keep right and onto Montreal Rd",
        "Continue on Montreal Rd",
        "At the light turn right onto Shefford Rd",
        "Continue on Shefford Rd",
        "At the light go straight",
        "Pull into the DriveTest Centre at the intersection",
    ],
    ("canotek", "G", 1): [
        "Turn left onto Shefford",
        "At the light continue straight",
        "At the 2nd light, turn right onto Montreal Rd",
        "Continue on Montreal Rd",
        "At the light, continue straight",
        "At the 2nd light, turn left onto Ogilvie Rd",
        "At the light, continue straight",
        "At the second light, continue straight",
        "At the third light, continue straight",
        "At the fourth light, continue straight",
        "At the fifth  light, continue straight",
        "At the sixth light, continue straight",
        "At the 7th  light, turn left onto 27",
        "At the light, continue straight",
        "At the 2nd light, turn left onto to highway ramp",
        "Continue on the Queensway",
        "Exit off onto Ch. de Montreal Rd exit ramp",
        "At the light turn left onto Montreal Rd",
        "At the light continue straight",
        "At the 2nd light, turn right onto Shefford",
        "At the light continue straight",
        "Pull into the DriveTest Centre at the intersection",
    ],
    ("walkley", "G2", 0): [
        "At the back of the @Centre,there is a lot to perform a parallel park and 3 point turn",
        "Turn right onto Walkey rd",
        "Continue on Walkey rd",
        "At the light, turn right onto Heatherington Rd",
        "Continue on Heatherington Rd",
        "At the stop sign, Continue on Heatherington Rd",
        "At the 2nd stop sign, turn left onto Fairlea Cres",
        "Mid way through Fairlea Cres, perform uphill/downhil park",
        "Continue on Fairlea Cres",
        "At the stop sign turn right onto Heatherington Rd",
        "At the light turn left onto walkey rd",
        "Continue walkey rd",
        "At the light turn right onto Baycrest Dr",
        "Continue on Baycrest Dr",
        "At the stop sign turn left onto Crederwood Dr",
        "Continue on Crederwood Dr",
        "At the light turn left onto Walkley rd",
        "Continue on Walkley rd",
        "At the light, continue Straight",
        "Continue on Walkley rd",
        "Pull into the DriveTest Centre at the intersection",
    ],
    ("walkley", "G2", 1): [
        "At the back of the @Centre,there is a lot to perform a parallel park and 3 point turn",
        "Turn left onto the median",
        "When safe to do so, turn right onto the walkley rd",
        "Continue walkey rd",
        "At the first light, continue on walkway rd",
        "At the second light turn right onto Cedarwood Dr",
        "Continue on Cedarwood Dr",
        "At the stop sign, turn left onto Baycrest Dr",
        "Continue on Baycrest Dr",
        "At the light, turn right onto Heron Rd",
        "Continue on Heron Rd",
        "At the light, turn left onto Briar Hill Dr",
        "Continue on Briar Hill Dr",
        "Perform Uphill/Downhill on Briar Hill Dr",
        "Continue on Briar Hill Dr",
        "At the stop sign, turn Right onto Featherston Dr",
        "Continue on Featherston Dr",
        "At the stop sign, turn right onto Jefferson St",
        "Continue on Jefferson St",
        "At the light, turn left onto Heron Rd",
        "Continue on Heron Rd",
        "At the light, use the filter and turn right onto Walkey Rd",
        "Continue on Walkey Rd",
        "At the light, Continue on Walkey Rd",
        "Turn onto the median and when safe to do so turn left onto DriveTest Centre",
    ],
    ("walkley", "G2", 2): [
        "At the back of the @Centre,there is a lot to perform a parallel park and 3 point turn",
        "Turn left onto the median",
        "When safe to do so, turn right onto the walkley rd",
        "Continue walkey rd",
        "At the light turn right onto Baycrest Dr",
        "Continue on Baycrest Dr",
        "At the stop sign Continue straight on Baycrest Dr",
        "At the light, turn right onto Heron Rd",
        "Continue on Heron Rd",
        "At the light, turn left onto Briar Hill Dr",
        "Continue on Briar Hill Dr",
        "Perform Uphill/Downhill on Briar Hill Dr",
        "Continue on Briar Hill Dr",
        "At the stop sign, turn Right onto Featherston Dr",
        "Continue on Featherston Dr",
        "At the stop sign, turn right onto Jefferson St",
        "Continue on Jefferson St",
        "At the light, turn right onto Heron Rd",
        "Continue on Heron Rd",
        "At the light, Continue on Heron Rd",
        "At the 2nd light, turn left onto Baycrest Dr",
        "Continue on Baycrest Dr",
        "At the stop sign, turn right onto Cedarwood Dr",
        "Continue on Crederwood Dr",
        "At the light turn left onto Walkley rd",
        "Continue on Walkley rd",
        "At the light, continue Straight",
        "Continue on Walkley rd",
        "Pull into the DriveTest Centre at the intersection",
    ],
    ("walkley", "G", 0): [
        "Turn left onto the median",
        "When safe to do so, turn right onto Walkey Rd",
        "Continue on Walkey Rd",
        "lots of lights, keep straight on Walkey Rd",
        "Right after your cross the bridge, turn to the left lane, and turn left onto Airport Parkway enter ramp",
        "Continue on Airport Parkway",
        "Get off the prom. Uplands Dr. exit ramp",
        "Get the light, turn left onto  Uplands Dr",
        "Continue on Uplands Dr",
        "At the light, Turn left onto Airport Parkway enter ramp",
        "Continue on Airport Parkway",
        "Get off the Walkley Rd exit ramp",
        "At the light, turn right onto Walkley Rd",
        "Continue on Walkley Rd",
        # transcript ends here (truncated, no return-to-centre text) --
        # kept as-is rather than inventing a closing line.
    ],
    ("walkley", "G", 1): [
        "Turn left onto the median",
        "When safe to do so, turn right onto Walkey Rd",
        "Continue on Walkey Rd",
        "lots of lights, keep straight on Walkey Rd",
        "Right after your cross the bridge, turn to the left lane, and turn left onto Airport Parkway enter ramp",
        "Continue on Airport Parkway",
        "Exit off the Hunt Club Road Exit ramp",
        "At the filter, go turn onto Hunt club",
        "At the light, continue on hunt club",
        "At the 2nd light turn right onto Uplands Drive",
        "Continue Uplands drive",
        "At the stop sign, turn right onto Paul Anka Dr",
        "Continue on Paul Anka Dr",
        "At the light turn right onto Mc Karthy Rd",
        "Continue on Mc Karthy Rd",
        "At the stop sign, Continue on Mc Karthy Rd",
        "At the light, turn left onto Hunt Club",
        "Continue on Hunt Club",
        "At the light, turn left onto Airport Parkway enter ramp",
        "Continue on Airport Parkway",
        "Get off the Walkley rd exit ramp",
        "At the light turn right onto Walkley rd",
        "Continue on Walkley rd",
        "Pull into the DriveTest Centre at the intersection",
    ],
    ("walkley", "G", 2): [
        "Turn right onto walkey rd",
        "Continue on walkey rd",
        "At the light, turn left onto Heatherington rd",
        "At the stop sign, continue on Heatherington rd",
        "At the 2nd stop sign, continue on Heatherington rd",
        "At the 3rd stop sign, continue on Heatherington rd",
        "At the 4th stop sign, turn left onto Albion rd N",
        "Continue on Albion rd N",
        "At the stop sign, turn right onto Kitchener Ave",
        "Continue on Kitchener Ave",
        "At the stop sign, continue on Kitchener Ave",
        "Turn right onto Banaf Ave",
        "Continue on Banaf Ave",
        "At the intersection, turn left onto  St Paul Ave",
        "Continue on St Paul Ave",
        "At the stop sign, turn left onto Bank St",
        "Continue on Bank St",
        "At the light, Continue on Bank St",
        "At the 2nd light, Continue on Bank St",
        "At the 3rd light, turn right onto Hunt Club Rd W",
        "Continue on Hunt Club Rd W",
        "At the light, continue on Hunt Club Rd W",
        "At the intersection turn right onto the prom. Airport PKwy enter ramp",
        "Continue on Airport Parkway",
        "Exit off the Walkey rd exit ramp",
        "At the light, turn right onto Walkey rd",
        "Continue on Walkley rd",
        "Pull into the DriveTest Centre at the intersection",
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


def resolve(centre, streets, g, overrides=None, pause=0.3):
    centre_ll = CENTRE_COORDS[centre]
    pts = [{"lat": centre_ll[0], "lon": centre_ll[1]}]
    failed = []
    forced_spurs = []
    last_key = None
    cur_lat, cur_lon = centre_ll
    overrides = list(overrides or [])
    for a, b in zip(streets, streets[1:]):
        # A street PAIR can meet at more than one real point -- a
        # crescent that loops off a road and back onto it has TWO real
        # junctions with that road, and "nearest to current position"
        # isn't necessarily the one actually driven (owner caught this:
        # Heatherington Rd x Fairlea Crescent has 2 real junctions ~200m
        # apart, "2nd stop sign" per the video means the FARTHER one,
        # not the nearer default pick). overrides lets a specific
        # occurrence be pinned to a real coordinate instead of guessed.
        ov = next((o for o in overrides
                   if _base(o[0]) == _base(a) and _base(o[1]) == _base(b)), None)
        if ov:
            overrides.remove(ov)
            for lat, lon in ov[2]:
                last_key = _add(pts, last_key, lat, lon)
            cur_lat, cur_lon = ov[2][-1]
            continue
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


def label_missing_spurs(steps, forced_spurs, streets):
    """A forced spur point makes OSRM actually DRIVE the street (geometry
    confirmed by a 0m-distance check against the street's real OSM nodes
    -- see manual_routes_report.md), but OSRM's own auto-generated
    instruction TEXT can still fail to name it: if the path glides
    through without a sharp turn, OSRM merges it into "continue onto
    <neighbour>" instead. That's a real defect in the user-facing
    turn-by-turn table (Cedarwood Dr was found completely absent from
    Walkley G2 route1's instructions this way), not just a cosmetic
    nuance -- riders read this table, not the raw coordinates. Insert an
    explicit zero-length marker step wherever a forced spur's name never
    appears in any step's instruction."""
    consumed = set()
    for spur in forced_spurs:
        if any(_base(spur) in _base(s["instruction"]) for s in steps):
            continue
        try:
            i = streets.index(spur)
        except ValueError:
            continue
        nxt = streets[i + 1] if i + 1 < len(streets) else None
        insert_at = None
        for j, s in enumerate(steps):
            if j in consumed:
                continue
            if nxt and _base(nxt) in _base(s["instruction"]):
                insert_at = j
                break
        if insert_at is None:
            continue
        consumed.add(insert_at)
        steps.insert(insert_at, {
            "instruction": f"Continue onto {spur}",
            "distance_m": 0, "duration_s": 0,
            "traffic_control": None, "speed_limit": None,
        })
    return steps


def _mentions(text, street):
    """Fuzzy: does `text` mention `street`, allowing for the owner's own
    spelling ("Tulon" for "Toulon", "Crederwood" for "Cedarwood")? Compares
    the street's first significant word against every word in text with
    SequenceMatcher rather than exact substring -- exact would miss every
    transcript typo this whole pipeline exists to route around."""
    import difflib
    words = _base(street).split()
    if not words:
        return False
    key = words[0]
    return any(difflib.SequenceMatcher(None, w, key).ratio() > 0.75
               for w in _norm(text).split())


def align_transcript(lines, osrm_steps, streets):
    """Keep the owner's own wording as the instruction text, but pull
    the REAL per-leg distance/duration/traffic_control/speed_limit from
    OSRM's own steps instead of leaving them blank -- owner asked for
    both (authentic wording, real numbers), not one or the other.

    Walks `streets` (the resolved, real sequence used for routing) and
    `osrm_steps` in lockstep to know which OSRM step covers which street;
    walks `lines` and `streets` in lockstep the same way to know which
    transcript line is the FIRST to mention each street (repeat "continue
    on X" lines for a street already entered stay blank -- the real
    distance was already shown once, repeating it would be misleading,
    not just redundant)."""
    step_for_street = {}
    sj = 0
    for i, s in enumerate(streets):
        while sj < len(osrm_steps) and not _mentions(osrm_steps[sj]["instruction"], s):
            sj += 1
        if sj < len(osrm_steps):
            step_for_street[i] = osrm_steps[sj]
            sj += 1

    out = []
    si = 0
    claimed = set()
    for line in lines:
        street_i = None
        for j in (si, si + 1):
            if j < len(streets) and _mentions(line, streets[j]):
                street_i = j
                si = j
                break
        step = step_for_street.get(street_i) if street_i not in claimed else None
        if street_i is not None:
            claimed.add(street_i)
        out.append({
            "instruction": line,
            "distance_m": step["distance_m"] if step else None,
            "duration_s": step["duration_s"] if step else None,
            "traffic_control": step["traffic_control"] if step else None,
            "speed_limit": step["speed_limit"] if step else None,
        })
    return out


def main():
    cg.load_known(DB_PATH)
    g = cg.Graph(DB_PATH)
    os.makedirs(OUT_DIR, exist_ok=True)

    by_centre = {}
    all_failed = {}
    for (centre, cls, idx), streets in ROUTES.items():
        pts, failed, forced_spurs = resolve(
            centre, streets, g, overrides=JUNCTION_OVERRIDES.get((centre, cls, idx)))
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
        if steps:
            steps = label_missing_spurs(steps, forced_spurs, streets)

        lines = TRANSCRIPT.get((centre, cls, idx))
        if lines:
            steps = align_transcript(lines, steps, streets)

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
        # deletes BOTH the old rebuild_routes rows (first-ever apply) and
        # any earlier manual_youtube rows (a later re-apply after fixing
        # a substitution) -- a re-apply that only matched 'rebuild_routes'
        # left the previous manual_youtube rows in place and duplicated
        # the whole centre (hit this for real: 26 rows instead of 13,
        # fixed by hand via fix_duplicate_rows.py, see git history).
        cur.execute(
            "DELETE FROM route_lines WHERE centre_id = %s "
            "AND source IN ('rebuild_routes', 'manual_youtube')",
            (centre,))
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
