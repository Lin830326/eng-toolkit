# Security policy

## Supported versions

Only the latest commit on `main` receives security fixes. There are no maintained release branches yet.

## Reporting a vulnerability

Report it privately: open the repository's **Security** tab and choose **Report a vulnerability**
(<https://github.com/Lin830326/eng-toolkit/security/advisories/new>). Do not open a public issue or pull request.

Include the tool name, the arguments or the input file that trigger the problem, the transport (stdio or HTTP), your
operating system, and the output of `eng-toolkit doctor`.

## What counts

eng-toolkit runs on the user's machine, takes arguments chosen by an AI agent, reads files the caller names, and starts
external programs (KiCad, ngspice, Gmsh, CalculiX, Yosys) as separate processes. These are in scope:

* reading or writing a file outside the workspace or outside the directories allowed by `ENG_TOOLKIT_READ_DIRS`;
* running a command or code that the caller supplied through a tool argument or an input file;
* an input that makes a tool hang, exhaust memory or crash the server instead of returning a structured refusal;
* a dependency that breaks the license policy without `toolkit_doctor` reporting it.

A wrong numerical result is a bug, not a vulnerability. Report it as a normal issue with the value you expected and its
source. Every result is marked `review_status: "unreviewed"` and must be checked by a qualified engineer before use.
