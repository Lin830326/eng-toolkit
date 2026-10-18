# Fit links in metrology_stackup_1d: design rationale

Produced under the pstack `poteto-mode` Feature playbook (architect step run by hand).

## Problem

A real dimension chain often contains the clearance of a hole/shaft pair, for example the radial play of a bearing seat. Today the agent must call `metrology_fit_limits`, read the minimum and maximum clearance, convert them into a nominal and a plus/minus pair by hand, and retype them into the chain. That hand conversion is where sign errors happen (a clearance of 7 to 41 micrometres has a nominal of 0 and a plus of 41 and a minus of -7, and an interference fit has a negative plus).

## Usage (caller's view)

```text
chain = [
  {"name": "housing", "nominal": "50 mm", "tol": "0.10 mm", "direction": "+"},
  {"name": "seat play", "fit": "H7/g6", "nominal": "25 mm", "direction": "-"}
]
metrology_stackup_1d(chain=..., method="worst_case")
  -> closing dimension 49.859 .. 50.093 mm
```

For a fit link, `nominal` is the diameter of the hole/shaft pair (3 to 500 mm). The link itself is the clearance, hole size minus shaft size: its nominal is 0 and its band runs from the fit's minimum to its maximum clearance. `direction` works as for any link. A fit link must not also carry `tol`, `plus` or `minus`.

## Shape

* `iso286.fit_limits` stays the only owner of the ISO 286 numbers. `stackup.py` stays free of ISO 286 knowledge.
* `stackup.Link` already represents a link as nominal, plus and minus. A fit link is the same link with nominal 0, `plus = max_clearance` and `minus = -min_clearance`. The only change in `stackup.py` is to accept a negative plus or minus as long as the band is not empty (`plus + minus >= 0`). The mid-point and half-range formulas and all three methods are unchanged and already correct for such a band.
* `tools.py` resolves a `fit` link into a plain `Link` while parsing the chain. The result lists the contribution with `kind` ("dimension" or "fit") and the fit code, so the caller can see where each band came from.
* The manual `tol`, `plus` and `minus` fields keep the rule that tolerances are magnitudes `>= 0`, so a typo such as `minus: "-0.1 mm"` still fails loudly. Only fit-derived links may have a band that does not contain the nominal.

## Alternatives considered

1. **A class link** (`"class": "H7"` giving the hole's own deviations). Closer to how drawings are dimensioned, but it answers a different question than the one asked, and the class deviations still need a separate hole and a separate shaft link. Left as a later step; the same resolution hook would serve it.
2. **A separate tool that turns a fit into a link** (`fit_to_link`) for the agent to paste. One more tool for a small model to learn, and the sign mistakes would move into the paste step. Rejected.
3. **Let manual links have negative plus or minus.** Flexible, but removes the guard that catches a mistyped sign. Rejected; only the fit path needs it.

## Tradeoffs accepted

* A fit link reuses the `nominal` field for the diameter, which is not the link's own nominal. We accept the small naming oddity in exchange for one set of field names; the tool description and error messages say so.
* The fit link is treated like any other independent link by RSS and Monte Carlo. A fit's clearance band is the combination of two tolerances, and treating it as one independent variable is the usual drawing-office approximation.

## Process notes

* `how`: skipped, the subsystem was read in full earlier in the session.
* `architect`: the arena fan-out was not run. The shape is a small extension, and the competing shapes are listed above.
* Implementation is delegated to a `poteto-agent` subagent and reviewed here.
