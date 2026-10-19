# Metric coarse-thread pitch table: source and license

`mech_bolt_check` looks up the thread pitch of a standard size when the caller gives only the nominal diameter. The table is the
constant `COARSE_PITCH_MM` in `eng_toolkit/domains/mech/fasteners.py`: **20 numbers**.

| d (mm) | 1.6 | 2 | 2.5 | 3 | 4 | 5 | 6 | 8 | 10 | 12 | 14 | 16 | 20 | 24 | 30 | 36 | 42 | 48 | 56 | 64 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P (mm) | 0.35 | 0.4 | 0.45 | 0.5 | 0.7 | 0.8 | 1 | 1.25 | 1.5 | 1.75 | 2 | 2 | 2.5 | 3 | 3.5 | 4 | 4.5 | 5 | 5.5 | 6 |

## Source and license

* The values are the coarse pitches of the ISO general-purpose metric screw thread series (ISO 261 selects the diameters, ISO 262
  the pitches). They are **geometric facts** published in every machinist's handbook and thread chart; the repo contains no text,
  figure, layout or other table of those standards (no tolerances, no minor/pitch diameters, no fine pitches).
* Typed in by hand for this project, licensed with the rest of the repo (Apache-2.0). The standard itself is the authority; check
  a size against it before relying on it for anything safety related. Every output is `review_status: "unreviewed"`.
* Cross-check done in `tests/test_mech_bolt.py`: the stress area `A_s = pi/4 (d - 0.9382 P)^2` computed from these pitches
  reproduces the commonly printed catalogue values of M8 (36.6), M10 (58.0), M12 (84.3), M16 (157), M20 (245) and M24 (353) mm^2
  within 0.3 %, which catches a wrong pitch for those six sizes. The other 14 entries are not covered by that check.
* Diameters not in the table (M18, M22, M27, inch threads, fine pitches) are not guessed: the caller must pass `pitch`.
