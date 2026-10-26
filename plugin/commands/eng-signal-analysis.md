---
description: Spectrum, peak identification, aliasing, filter design and decimation of a sampled record with the eng-toolkit tools
argument-hint: <describe the record (file, sample rate, units), the machine and what to find>
---

Use the `eng-signal-analysis` skill (and `eng-toolkit` for the unit and reporting rules) to analyse this signal-processing problem with the eng-toolkit MCP tools:

$ARGUMENTS

If key inputs are missing (sample rate, file layout (one value column or time,value; one header line of names is fine), units, rpm or line frequency, tooth or pole counts, the band to keep and the attenuation required, any analog filter before the converter), ask for them first; otherwise run the analysis, convert rpm to Hz, name each peak by a rule, check for aliasing, and give the report in the user's language.
