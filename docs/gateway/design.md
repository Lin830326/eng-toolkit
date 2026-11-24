# Shared gateway input boundary

Every mounted domain and the gateway's own tools use `ArgumentRefusals` middleware.
FastMCP validates function arguments before the domain runner can execute. The
middleware translates Pydantic validation failures into the usual `ToolResult`,
with `ok=false`, `review_status="unreviewed"` and `error.details.param` as an
escaped JSON pointer. MCP `is_error` remains false, as for domain refusals.

Missing required arguments use `input.missing`. Wrong types and unknown arguments
use `input.invalid`. A JSON number supplied to a quantity-string field uses
`units.bare_number`. The current schemas describe quantities through numeric-with-unit
examples in their parameter descriptions or defaults, so the middleware uses those examples
to distinguish quantities from modes, file paths and record strings. New quantity
fields must retain such an example. Framework validation still decides whether
the arguments are accepted; the middleware does not duplicate or relax it.

The first supplied invalid argument takes precedence over missing arguments.
Messages name the expected type without copying the offending value or Pydantic's
traceback. Unexpected argument names longer than 256 characters use `/` as the
argument-object diagnostic pointer to keep refusals bounded. These pre-execution
refusals have gateway provenance and create no calculation job.

## Offset temperatures

`units.parse_quantity` checks unit spelling before Pint parses a compound
expression. It resolves aliases through the shared registry and rejects offset
temperature units in products, quotients and powers, including factors that would
cancel or have exponent zero. This applies to quantity strings, mappings and
JSON-encoded mappings. It prevents Pint from silently substituting delta units.
The refusal is `units.dimension_mismatch` with the parameter pointer.

A plain absolute temperature such as `20 degC` still converts to 293.15 K.
Compounds may use K, degR or explicit delta units. For example, `delta_degF/K`
has the scale factor 5/9; `degF/K` is refused. A preconstructed Pint quantity
whose offset spelling has already been lost cannot reveal the original input;
gateway schemas accept strings so this ambiguity does not occur on MCP calls.

## JSON artifacts

Before writing inputs and hashing them, the runner recursively replaces nonfinite
floats with `"NaN"`, `"Infinity"` or `"-Infinity"`, and repairs lone UTF-16
surrogates. The saved inputs and provenance hash use the same representation.
Calculations still receive their original arguments and apply their normal
validation, including validation of finite ranges. An unused parameter may
therefore remain recorded as text on a successful call.

All shared JSON artifact writers preconvert nonfinite values and use
`allow_nan=False`, including result and manifest files. They never emit bare
NaN or Infinity tokens. This serialization rule does not certify a nonfinite
input as a valid engineering value.

## Verification

`tests/test_gateway_validation.py` reproduces electric review finding 6 and
fatigue review findings 5, 6 and 7 through `Client(gateway)`. It checks wrong
types in every discovered domain, unknown arguments for every registered tool,
bounded errors, offset aliases and cancellation, valid absolute/delta temperature
calculations, strict JSON artifact parsing and matching input hashes. The
Ramberg-Osgood reference uses exact rational arithmetic. The shared-layer changes
also require the complete test suite, including surrogate, workspace and contract
checks.

## Pictures in the reply (`InlineImages`)

A tool that writes a PNG returns only its path, and a chat client cannot open a path. Every tool that declares the boolean
parameter `inline_image` (default true: `plot_xy`, `plot_panels`, `plot_bar`, `geometry_view`, `fem_view`) is wrapped by the
`InlineImages` middleware (`eng_toolkit/gateway/images.py`; the shared constants and the parameter text are in
`eng_toolkit/inline_image.py`). After a successful call it adds one MCP `ImageContent` block (base64, `image/png`) after the
`TextContent`; `structured_content` (the unified `ToolResult`) is the same, `data.png_path` stays.

* The file read is the result's own PNG artifact: it must lie in `data.job_dir` and its SHA-256 must match the artifact record.
* A PNG above 600 000 bytes is not attached; the returned `warnings` (and the text block) get `The PNG is N kB, above the 600 kB limit
  for an inline image ... open it from data.png_path`. `result.json` on disk is not changed.
* `inline_image=false`, a refusal, a result without a PNG (`plot_*` with `format="svg"`, `preview="none"`) and an unreadable file
  all return the plain result. The parameter is not part of the hashed inputs.
* Tested through an in-process FastMCP client in `tests/test_plot_inline_image.py`.
