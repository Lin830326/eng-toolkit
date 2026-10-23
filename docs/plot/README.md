# Plot tools

`plot_xy`, `plot_panels`, and `plot_bar` write a self-contained `plot.svg` and a `plot_data.json` file with the chart data and
axis mappings. Results carry `review_status: "unreviewed"`. See [the design notes](design.md) for input formats, limits, and layout rules.

Fixed ranges clip lines, fills, and stems to the visible frame. A stem starts at zero on a linear axis, even when zero is outside
the selected range. Its line can remain visible when the marker lies outside the frame. On a log axis, stems start at the bottom.

Histogram values and explicit bin edges use exact integer and decimal comparisons. Bins include their lower edge and exclude
their upper edge, except that the last bin includes both edges. For example:

```json
{
  "mode": "histogram",
  "series": "{\"y\":[9007199254740992,9007199254740993,9007199254740994]}",
  "bin_edges": "0,9007199254740993,10000000000000000"
}
```

This returns counts `[1, 2]`. CSV input follows the same rules. `data.histogram.bin_edges` contains numeric edges, with integers
preserved. `bin_edges_exact` contains their exact decimal spellings for clients that parse all JSON numbers as binary64.
Bin edges too close to distinguish after conversion for drawing, or nonzero edges that underflow to zero, return
`input.out_of_range` at `/bin_edges`.

Density uses only values within the selected edges and integrates to one over those bins. If none fall inside, density is undefined
and returns `input.invalid` at `/bin_edges`. Counts and cumulative counts can still return all zeros.

Numeric category labels must be finite and have magnitude at most 1e100, just like numeric values. Duplicate JSON keys and unknown
keys receive `input.invalid` refusals with parameter pointers. Automatic log ranges whose expansion underflows return
`input.out_of_range` at the data pointer; rescale the data or supply a positive, representable fixed range.
