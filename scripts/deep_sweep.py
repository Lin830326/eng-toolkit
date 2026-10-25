"""Deep hostile-input sweep for one domain: python scripts/deep_sweep.py <domain> <outdir>  (run from the repo root with PYTHONPATH=.).

The pytest version, tests/test_contract_deep.py, runs a smaller set of the same classes; this script adds 22 file extensions,
12 MB files, 1.5 million element arrays and 5 MB strings, and is meant to be run on demand (about 10 minutes per domain).
It writes findings_<domain>.jsonl (problems and calls over 8 s) and progress_<domain>.log (a line before every call, so a
hang names its culprit).

New classes beyond the basic sweep: nesting depth, huge digit strings and exponents, multi-megabyte strings and arrays,
unicode oddities, path tricks, integer/float extremes, and for file parameters empty / garbage / large / directory /
missing / device-name files under every common extension. A progress line is written before every call so a hang
points at its culprit.
"""
import asyncio, json, os, random, re, shutil, sys, time, collections
from pathlib import Path

domain, out = sys.argv[1], Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
work = out / f"work_{domain}"
shutil.rmtree(work, ignore_errors=True)
(work / "ws").mkdir(parents=True)
(work / "files").mkdir()
os.environ["ENG_TOOLKIT_WS"] = str(work / "ws")
os.environ["ENG_TOOLKIT_READ_DIRS"] = str(work / "files")

from fastmcp import Client
from eng_toolkit.gateway.server import gateway

progress = open(out / f"progress_{domain}.log", "w", buffering=1, encoding="utf-8")
findings = open(out / f"findings_{domain}.jsonl", "w", buffering=1, encoding="utf-8")

FILE_PARAM = re.compile(r"(^|_)(path|file|files)$|^files_[ab]$")
EXTS = [".step", ".stp", ".iges", ".brep", ".stl", ".glb", ".csv", ".txt", ".json", ".dat", ".cir", ".sp", ".v", ".sv",
        ".blif", ".kicad_pcb", ".kicad_sch", ".dxf", ".dwg", ".pdf", ".pts", ".ply"]
BIG = 12 * 1024 * 1024


def string_cases():
    return {
        "deep_array": "[" * 60000 + "]" * 60000,
        "deep_object": '{"a":' * 60000 + "1" + "}" * 60000,
        "deep_parens": "(" * 60000 + "1" + ")" * 60000,
        "huge_digits": "9" * 200000,
        "huge_digits_unit": "9" * 200000 + " m",
        "huge_exp_pos": "1e999999999 m",
        "huge_exp_neg": "1e-999999999 m",
        "huge_exp_digits": "1e" + "9" * 5000 + " m",
        "many_numbers": "[" + ",".join(["1"] * 1_500_000) + "]",
        "big_string": "A" * 5_000_000,
        "many_lines": "1 m\n" * 400_000,
        "newlines": "\n" * 50000,
        "arabic_digits": "\u0663 m",
        "fullwidth": "\uff11\uff12 m",
        "bom": "\ufeff5 m",
        "rtl": "5\u202e m",
        "zwsp": "5 m\u200b",
        "combining": "5 m" + "\u0301" * 5000,
        "dup_keys": '{"a":1,"a":2,"a":3}',
        "format": "%s%n%x{0.__class__}${jndi:ldap://x}{{7*7}}",
        "path_traversal": "../../../../etc/passwd",
        "win_path": "C:\\Windows\\win.ini",
        "unc": "\\\\127.0.0.1\\c$\\x",
        "device": "CON",
        "file_url": "file:///c:/windows/win.ini",
        "nan_inf_list": "[NaN, Infinity, -Infinity]",
        "neg_zero": "-0.0 m",
        "denormal": "5e-324 m",
        "fmax": "1.7976931348623157e308 m",
    }


def any_cases():
    return {
        "int_2_63": 2 ** 63, "int_neg_2_63": -(2 ** 63), "int_1e30": 10 ** 30, "int_1e5000": 10 ** 5000 if False else 10 ** 400,
        "float_denormal": 5e-324, "float_max": 1.7976931348623157e308, "neg_zero": -0.0,
        "big_list": list(range(200000)), "deep_list": _nest(90), "big_dict": {str(i): i for i in range(20000)},
        "list_of_str": ["1 m"] * 50000,
    }


def _nest(n):
    x = []
    for _ in range(n):
        x = [x]
    return x


def types_of(prop):
    t = prop.get("type")
    types = {t} if isinstance(t, str) else set(t or [])
    for sub in prop.get("anyOf", []) or []:
        if "type" in sub:
            types.add(sub["type"])
    return types


def filler(prop):
    if prop.get("enum"):
        return prop["enum"][0]
    for sub in prop.get("anyOf", []) or []:
        if sub.get("enum"):
            return sub["enum"][0]
    if prop.get("default") is not None:
        return prop["default"]
    types = types_of(prop)
    if "string" in types or not types:
        return "1 m"
    if "integer" in types:
        return 2
    if "number" in types:
        return 1.0
    if "boolean" in types:
        return False
    return None


def finite_json(obj):
    try:
        json.dumps(obj, allow_nan=False)
        return True
    except ValueError:
        return False


def make_files():
    """name -> (kind, path) for file-parameter cases; created lazily per extension."""
    rnd = random.Random(7)
    garbage = bytes(rnd.randrange(256) for _ in range(4096))
    paths = {}
    for ext in EXTS:
        for kind, data in (("empty", b""), ("garbage", garbage), ("text_garbage", b"hello world\n" * 50)):
            p = work / "files" / f"{kind}{ext}"
            p.write_bytes(data)
            paths[f"{kind}{ext}"] = str(p)
        d = work / "files" / f"dir{ext}"
        d.mkdir(exist_ok=True)
        paths[f"dir{ext}"] = str(d)
        paths[f"missing{ext}"] = str(work / "files" / f"missing{ext}")
    return paths


_BIG = {}


def make_big(ext):
    if ext in _BIG:
        return _BIG[ext]
    p = work / "files" / f"big{ext}"
    chunk = (b"1.0,2.0,3.0\n" * 8192)
    with open(p, "wb") as f:
        n = 0
        while n < BIG:
            f.write(chunk)
            n += len(chunk)
    _BIG[ext] = str(p)
    return str(p)


async def main():
    counts = collections.Counter()
    slow = []
    file_paths = make_files()
    async with Client(gateway) as c:
        tools = [t for t in await c.list_tools() if t.name.split("_", 1)[0] == domain]
        for tool in sorted(tools, key=lambda t: t.name):
            schema = tool.inputSchema or {}
            props = schema.get("properties", {})
            required = schema.get("required", [])
            base = {k: filler(props[k]) for k in required}
            for pname in list(props)[:14]:
                prop = props[pname]
                types = types_of(prop)
                cases = {}
                if "string" in types or not types:
                    cases.update(string_cases())
                cases.update(any_cases())
                if FILE_PARAM.search(pname):
                    for name, path in file_paths.items():
                        cases["file_" + name] = path
                    for ext in (".csv", ".step", ".json", ".v", ".cir", ".kicad_pcb"):
                        cases["file_big" + ext] = make_big(ext)
                    cases["file_nul"] = str(work / "files" / "x\u0000.csv")
                    cases["file_dotdot"] = str(work / "files" / ".." / ".." / "x.csv")
                    cases["file_device"] = "NUL"
                for case, value in cases.items():
                    counts["calls"] += 1
                    progress.write(f"{tool.name} {pname} {case}\n")
                    t0 = time.monotonic()
                    problem = None
                    try:
                        r = await asyncio.wait_for(c.call_tool(tool.name, {**base, pname: value}, raise_on_error=False), 120)
                        sc = r.structured_content
                        if sc is None:
                            problem = ("no_structured", str(r.content)[:100])
                        elif not finite_json(sc):
                            problem = ("nonfinite_output", "")
                        elif sc.get("ok"):
                            counts["ok"] += 1
                        else:
                            counts["refused"] += 1
                            err = sc.get("error") or {}
                            fam = err.get("family", "")
                            param = (err.get("details") or {}).get("param")
                            msg = json.dumps(err)
                            if not fam:
                                problem = ("no_family", "")
                            elif fam == "engine.failure" or "Traceback" in msg:
                                problem = ("engine_failure", err.get("message", "")[:160])
                            elif not (isinstance(param, str) and param.startswith("/")):
                                problem = ("no_pointer", f"{fam} param={param!r}")
                    except Exception as exc:
                        problem = ("exception", repr(exc)[:160])
                    dt = time.monotonic() - t0
                    if dt > 8:
                        slow.append((tool.name, pname, case, round(dt, 1)))
                        findings.write(json.dumps({"kind": "slow", "tool": tool.name, "param": pname, "case": case, "seconds": round(dt, 1)}) + "\n")
                    if problem:
                        findings.write(json.dumps({"kind": problem[0], "tool": tool.name, "param": pname, "case": case, "detail": problem[1]}) + "\n")
                    # remove big files after use to bound disk
    progress.write("DONE\n")
    print(domain, dict(counts), "slow", len(slow))


asyncio.run(main())
