"""
idl.py — a deliberately small IDL reader, just enough for contract checks.

It understands: modules, structs, enums, @key and other annotations, bounded
strings, comments and #include lines. Anything else (typedef, union, const,
sequences of structs...) is reported as "not checked" rather than guessed at.

parse(text) -> (types, notes)
  types = {"alerts::Alert": {"kind": "struct", "ext": "appendable",
                             "members": [("source", "string<64>", True), ...]},
           "alerts::Severity": {"kind": "enum", "members": ["SEVERITY_INFO", ...]}}
  notes = ["typedef at line 12 is not contract-checked", ...]

The same function reads our IDL files and the IDL text Connext prints for the
Python types, so the two can be compared like for like.
"""
from __future__ import annotations

import re

_TOKEN = re.compile(r'@\w+(?:\s*\([^)]*\))?|::|<\s*\d+\s*>|[{};,<>]|[A-Za-z_]\w*|\d+|"[^"]*"|\S')
EXT_ANNOTATIONS = {"final": "final", "appendable": "appendable", "extensible": "appendable", "mutable": "mutable"}
UNSUPPORTED = {"typedef", "union", "const", "bitmask", "bitset", "interface", "valuetype", "exception"}


class IdlError(ValueError):
    pass


def _clean(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//[^\n]*", " ", text)
    return re.sub(r"^\s*#[^\n]*", " ", text, flags=re.M)


def _norm_type(parts: list[str]) -> str:
    t = "".join(parts).replace(" ", "")
    t = re.sub(r"<(\d+)>", r"<\1>", t)
    return {"float64": "double", "float32": "float"}.get(t, t)


def parse(text: str, qualify: bool = True) -> tuple[dict, list[str]]:
    """Parse one IDL text. With qualify=False, member types stay as written and
    each struct keeps its scope, so several files can be resolved together
    with resolve()."""
    toks = _TOKEN.findall(_clean(text))
    types: dict = {}
    notes: list[str] = []
    scope: list[str] = []
    ann: list[str] = []
    i = 0

    def expect(tok):
        nonlocal i
        if i >= len(toks) or toks[i] != tok:
            got = toks[i] if i < len(toks) else "end of file"
            raise IdlError(f"expected '{tok}' but found '{got}'")
        i += 1

    while i < len(toks):
        t = toks[i]
        if t.startswith("@"):
            ann.append(re.sub(r"\s*\(.*", "", t[1:]))
            i += 1
        elif t == "module":
            scope.append(toks[i + 1])
            i += 2
            expect("{")
            ann = []
        elif t in ("struct", "enum"):
            name = "::".join(scope + [toks[i + 1]])
            i += 2
            if i < len(toks) and toks[i] == ";":         # forward declaration
                i += 1
                ann = []
                continue
            if i < len(toks) and toks[i] == ":":
                raise IdlError(f"struct inheritance in {name} is not supported by the contract checker")
            expect("{")
            if t == "enum":
                members = []
                while toks[i] != "}":
                    if toks[i] not in (",",) and not toks[i].startswith("@"):
                        members.append(toks[i])
                    i += 1
                types[name] = {"kind": "enum", "members": members}
            else:
                ext = "appendable"
                for a in ann:
                    ext = EXT_ANNOTATIONS.get(a, ext)
                members, buf, mem_ann = [], [], []
                while toks[i] != "}":
                    tk = toks[i]
                    if tk.startswith("@"):
                        mem_ann.append(re.sub(r"\s*\(.*", "", tk[1:]))
                    elif tk == ";":
                        if len(buf) < 2:
                            raise IdlError(f"can't read a member of {name} near '{' '.join(buf)}'")
                        members.append((buf[-1], _norm_type(buf[:-1]), "key" in mem_ann))
                        buf, mem_ann = [], []
                    else:
                        buf.append(tk)
                    i += 1
                    if i >= len(toks):
                        raise IdlError(f"struct {name} is not closed with '}}'")
                types[name] = {"kind": "struct", "ext": ext, "members": members, "_scope": list(scope)}
            i += 1
            expect(";")
            ann = []
        elif t in UNSUPPORTED:
            notes.append(f"'{t}' is not contract-checked")
            depth = 0
            while i < len(toks):                          # skip to the end of the declaration
                if toks[i] == "{":
                    depth += 1
                elif toks[i] == "}":
                    depth -= 1
                elif toks[i] == ";" and depth == 0:
                    break
                i += 1
            i += 1
            ann = []
        elif t == "}":
            if not scope:
                raise IdlError("unbalanced '}'")
            scope.pop()
            i += 1
            expect(";")
        else:
            i += 1
    if scope:
        raise IdlError(f"module {'::'.join(scope)} is not closed")
    if qualify:
        _qualify(types)
    return types, notes


def resolve(types: dict) -> None:
    """Qualify member types across several parsed files at once."""
    _qualify(types)


def _qualify(types: dict) -> None:
    """Resolve unqualified member types (e.g. `Severity` inside module alerts)
    to their full name (`alerts::Severity`), as IDL scoping rules do."""
    for spec in types.values():
        scope = spec.pop("_scope", None)
        if scope is None:
            continue
        resolved = []
        for mname, mtype, is_key in spec["members"]:
            if "::" not in mtype:
                for depth in range(len(scope), -1, -1):
                    candidate = "::".join(scope[:depth] + [mtype])
                    if candidate in types:
                        mtype = candidate
                        break
            resolved.append((mname, mtype, is_key))
        spec["members"] = resolved
