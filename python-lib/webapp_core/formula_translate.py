"""Best-effort Excel-formula -> Python/pandas expression translator.

The output is a *suggestion*: the Admin UI shows it in an editable textarea next to the
original formula, and the admin fixes anything the translator could not handle. Every
generated expression runs in the restricted namespace built by ``compute.evaluate`` which
provides: ``df`` (canonical + already-computed columns), ``np``, ``pd``, the helpers
``IF AND OR NOT ROUND ROUNDUP ROUNDDOWN ABS INT MOD SQRT POWER MIN MAX SUM AVERAGE COUNT
CONCAT LEFT RIGHT MID LEN UPPER LOWER TRIM TEXT VALUE YEAR MONTH DAY TODAY NOW ISBLANK
ISNUMBER ISERROR IFERROR VLOOKUP CELL`` and ``PARAM`` (toggle/named-value accessor).

Formulas are expected in the *generalized* form produced by ``sample_parser`` where the
data row number has been replaced by the ``{r}`` placeholder (e.g. ``=C{r}*D{r}``).
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

_TOKEN_SPEC = [
    ("WS", r"\s+"),
    ("STRING", r'"(?:[^"]|"")*"'),
    ("SHEETRANGE", r"(?:'[^']+'|[A-Za-z_][A-Za-z0-9_ ]*)!\$?[A-Za-z]{1,3}\$?\d+:\$?[A-Za-z]{1,3}\$?\d+"),
    ("SHEETCELL", r"(?:'[^']+'|[A-Za-z_][A-Za-z0-9_ ]*)!\$?[A-Za-z]{1,3}\$?\d+"),
    ("RELCELL", r"\$?[A-Za-z]{1,3}\{r\}"),
    ("ABSCELL", r"\$?[A-Za-z]{1,3}\$?\d+"),
    ("NUMBER", r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"),
    ("FUNC", r"[A-Za-z_][A-Za-z0-9_.]*(?=\s*\()"),
    ("IDENT", r"[A-Za-z_][A-Za-z0-9_.]*"),
    ("OP", r"<>|<=|>=|[-+*/^&=<>]"),
    ("LPAREN", r"\("),
    ("RPAREN", r"\)"),
    ("COMMA", r","),
]
_MASTER_RE = re.compile("|".join(f"(?P<{n}>{p})" for n, p in _TOKEN_SPEC))

_FUNC_MAP = {
    "IF": "IF", "IFERROR": "IFERROR", "AND": "AND", "OR": "OR", "NOT": "NOT",
    "ROUND": "ROUND", "ROUNDUP": "ROUNDUP", "ROUNDDOWN": "ROUNDDOWN",
    "ABS": "ABS", "INT": "INT", "MOD": "MOD", "SQRT": "SQRT", "POWER": "POWER",
    "MIN": "MIN", "MAX": "MAX", "SUM": "SUM", "AVERAGE": "AVERAGE", "COUNT": "COUNT",
    "CONCAT": "CONCAT", "CONCATENATE": "CONCAT",
    "LEFT": "LEFT", "RIGHT": "RIGHT", "MID": "MID", "LEN": "LEN",
    "UPPER": "UPPER", "LOWER": "LOWER", "TRIM": "TRIM", "TEXT": "TEXT", "VALUE": "VALUE",
    "YEAR": "YEAR", "MONTH": "MONTH", "DAY": "DAY", "TODAY": "TODAY", "NOW": "NOW",
    "ISBLANK": "ISBLANK", "ISNUMBER": "ISNUMBER", "ISERROR": "ISERROR",
    "COALESCE": "IFERROR",
}


class _Tok:
    __slots__ = ("kind", "val")

    def __init__(self, kind: str, val: str):
        self.kind, self.val = kind, val

    def __repr__(self):  # pragma: no cover - debug only
        return f"{self.kind}:{self.val}"


def _tokenize(s: str) -> List[_Tok]:
    out: List[_Tok] = []
    pos = 0
    while pos < len(s):
        m = _MASTER_RE.match(s, pos)
        if not m:
            raise ValueError(f"Cannot tokenize near: {s[pos:pos + 20]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "WS":
            continue
        out.append(_Tok(kind, m.group()))
    return out


def _sheet_name(ref: str) -> str:
    name = ref.split("!", 1)[0]
    if name.startswith("'") and name.endswith("'"):
        name = name[1:-1]
    return name


class _Parser:
    def __init__(self, tokens: List[_Tok], header_by_letter: Dict[str, str]):
        self.toks = tokens
        self.i = 0
        self.headers = header_by_letter
        self.notes: List[str] = []

    # -- token helpers
    def _peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _next(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def _eat(self, kind):
        t = self._peek()
        if not t or t.kind != kind:
            raise ValueError(f"Expected {kind}, got {t}")
        return self._next()

    # -- grammar
    def parse(self) -> str:
        expr = self._concat()
        return expr

    def _concat(self) -> str:
        left = self._compare()
        while self._peek() and self._peek().kind == "OP" and self._peek().val == "&":
            self._next()
            right = self._compare()
            left = f"CONCAT({left}, {right})"
        return left

    def _compare(self) -> str:
        left = self._addsub()
        while self._peek() and self._peek().kind == "OP" and self._peek().val in ("=", "<>", "<", ">", "<=", ">="):
            op = self._next().val
            py = {"=": "==", "<>": "!="}.get(op, op)
            right = self._addsub()
            left = f"({left} {py} {right})"
        return left

    def _addsub(self) -> str:
        left = self._muldiv()
        while self._peek() and self._peek().kind == "OP" and self._peek().val in ("+", "-"):
            op = self._next().val
            right = self._muldiv()
            left = f"({left} {op} {right})"
        return left

    def _muldiv(self) -> str:
        left = self._power()
        while self._peek() and self._peek().kind == "OP" and self._peek().val in ("*", "/"):
            op = self._next().val
            right = self._power()
            left = f"({left} {op} {right})"
        return left

    def _power(self) -> str:
        left = self._unary()
        while self._peek() and self._peek().kind == "OP" and self._peek().val == "^":
            self._next()
            right = self._unary()
            left = f"({left} ** {right})"
        return left

    def _unary(self) -> str:
        t = self._peek()
        if t and t.kind == "OP" and t.val in ("-", "+"):
            self._next()
            return f"({t.val}{self._unary()})"
        return self._primary()

    def _primary(self) -> str:
        t = self._peek()
        if t is None:
            raise ValueError("Unexpected end of formula")
        if t.kind == "LPAREN":
            self._next()
            inner = self._concat()
            self._eat("RPAREN")
            return f"({inner})"
        if t.kind == "NUMBER":
            return self._next().val
        if t.kind == "STRING":
            raw = self._next().val[1:-1].replace('""', '"')
            return repr(raw)
        if t.kind == "FUNC":
            return self._funccall()
        if t.kind == "RELCELL":
            letter = self._next().val.split("{")[0].replace("$", "").upper()
            col = self.headers.get(letter)
            if col is None:
                self.notes.append(f"column letter {letter} not in the data schema")
                return f"df[{letter!r}]"
            return f"df[{col!r}]"
        if t.kind == "ABSCELL":
            self._next()
            self.notes.append(
                f"fixed cell {t.val} on the data sheet cannot be resolved at compute time"
            )
            return "np.nan"
        if t.kind == "SHEETCELL":
            self._next()
            sheet = _sheet_name(t.val)
            a1 = t.val.split("!", 1)[1].replace("$", "")
            return f"CELL({sheet!r}, {a1!r})"
        if t.kind == "SHEETRANGE":
            self._next()
            return f"LOOKUP_TABLE({_sheet_name(t.val)!r})"
        if t.kind == "IDENT":
            name = self._next().val
            if name.upper() in ("TRUE", "FALSE"):
                return name.capitalize()
            return f"PARAM({name!r})"
        raise ValueError(f"Unexpected token {t}")

    def _funccall(self) -> str:
        fname = self._next().val.upper()
        self._eat("LPAREN")
        raw_args: List[str] = []
        arg_tokens: List[List[_Tok]] = []
        if self._peek() and self._peek().kind != "RPAREN":
            while True:
                start = self.i
                val = self._concat()
                raw_args.append(val)
                arg_tokens.append(self.toks[start:self.i])
                if self._peek() and self._peek().kind == "COMMA":
                    self._next()
                    continue
                break
        self._eat("RPAREN")

        if fname == "VLOOKUP":
            # VLOOKUP(key, Sheet!range, col_index, [approx])
            sheet = None
            for tk in arg_tokens[1] if len(arg_tokens) > 1 else []:
                if tk.kind in ("SHEETRANGE", "SHEETCELL"):
                    sheet = _sheet_name(tk.val)
                    break
            key = raw_args[0] if raw_args else "np.nan"
            col_index = raw_args[2] if len(raw_args) > 2 else "2"
            approx = raw_args[3] if len(raw_args) > 3 else "False"
            if sheet is None:
                self.notes.append("VLOOKUP table is not a Sheet!range reference")
                sheet = "UNKNOWN"
            return f"VLOOKUP({key}, {sheet!r}, {col_index}, {approx})"

        if fname in ("HLOOKUP", "INDEX", "MATCH", "XLOOKUP", "LOOKUP"):
            self.notes.append(f"{fname} needs manual translation")
            return f"MANUAL({fname!r})"

        if fname in ("TRUE", "FALSE"):
            return fname.capitalize()

        if fname == "IFS":
            # IFS(c1,v1,c2,v2,...) -> nested IF
            self.notes.append("IFS converted to nested IF")
            expr = "np.nan"
            for k in range(len(raw_args) - 2, -1, -2):
                expr = f"IF({raw_args[k]}, {raw_args[k + 1]}, {expr})"
            return expr

        mapped = _FUNC_MAP.get(fname)
        if mapped is None:
            self.notes.append(f"unknown function {fname} kept as-is")
            mapped = fname
        return f"{mapped}({', '.join(raw_args)})"


def translate(excel_formula: str, header_by_letter: Dict[str, str]) -> Tuple[str, List[str]]:
    """Return ``(python_expression, notes)``. Never raises - failures land in ``notes``."""
    s = excel_formula.strip()
    if s.startswith("="):
        s = s[1:]
    if not s:
        return "np.nan", ["empty formula"]
    try:
        parser = _Parser(_tokenize(s), header_by_letter)
        expr = parser.parse()
        if parser.i != len(parser.toks):
            parser.notes.append("trailing tokens were ignored")
        return expr, parser.notes
    except Exception as exc:  # noqa: BLE001 - best effort, surface to admin
        return f"np.nan  # TODO translate: {s}", [f"could not parse: {exc}"]
