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
