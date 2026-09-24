"""Read a WoW SavedVariables file as data.

SavedVariables are Lua source (`Name = { ["key"] = value, ... }`), but uploads are untrusted, so nothing is
executed: this is a parser for the data subset the client writes (assignments of nil, booleans, numbers, strings
and nested tables) and anything else, such as a function call or an expression, is rejected.
"""
import re

MAX_BYTES = 32 * 1024 * 1024
MAX_DEPTH = 32


class Malformed(ValueError):
    """The file isn't a SavedVariables data file (or is too big or too deep)."""


_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_ESCAPES = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34, "'": 39, "\n": 10}
_KEYWORDS = {"true": True, "false": False, "nil": None}


def loads(data: bytes) -> dict:
    """The file's top-level assignments as {name: value}; tables become lists (keys 1..n) or dicts."""
    if len(data) > MAX_BYTES:
        raise Malformed(f"{len(data)} bytes is over the {MAX_BYTES}-byte limit")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return _Parser(data.decode("latin-1")).file()  # latin-1 maps bytes 1:1; strings are re-decoded as UTF-8


class _Parser:
    def __init__(self, src: str):
        self.s, self.i = src, 0

    def fail(self, msg: str):
        line = self.s.count("\n", 0, self.i) + 1
        raise Malformed(f"line {line}: {msg}")

    def skip(self):
        s = self.s
        while self.i < len(s):
            c = s[self.i]
            if c in " \t\r\n\f\v":
                self.i += 1
            elif s.startswith("--", self.i):
                long = re.match(r"--\[(=*)\[", s[self.i:self.i + 64])
                if long:
                    end = s.find("]" + long[1] + "]", self.i)
                    if end < 0:
                        self.fail("unterminated comment")
                    self.i = end + len(long[1]) + 2
                else:
                    end = s.find("\n", self.i)
                    self.i = len(s) if end < 0 else end + 1
            else:
                return

    def peek(self) -> str:
        self.skip()
        return self.s[self.i:self.i + 1]

    def expect(self, c: str):
        if self.peek() != c:
            self.fail(f"expected {c!r}, got {self.s[self.i:self.i + 10]!r}")
        self.i += 1

    def file(self) -> dict:
        out = {}
        while self.peek():
            m = _NAME.match(self.s, self.i)
            if not m or m[0] in _KEYWORDS:
                self.fail("expected a variable assignment")
            self.i = m.end()
            self.expect("=")
            out[m[0]] = self.value(0)
            if self.peek() == ";":
                self.i += 1
        return out

    def value(self, depth: int):
        c = self.peek()
        if not c:
            self.fail("unexpected end of file")
        if c == "{":
            return self.table(depth + 1)
        if c in "\"'":
            return self.string()
        if c == "[" and re.match(r"\[=*\[", self.s[self.i:self.i + 64]):
            return self.long_string()
        m = _NAME.match(self.s, self.i)
        if m:
            if m[0] not in _KEYWORDS:
                self.fail(f"unexpected {m[0]!r}: only data is allowed")
            self.i = m.end()
            return _KEYWORDS[m[0]]
        sign = 1
        if c == "-":
            sign, self.i = -1, self.i + 1
        m = _NUMBER.match(self.s, self.i)
        if not m:
            self.fail(f"unexpected {self.s[self.i:self.i + 10]!r}")
        self.i = m.end()
        if _NAME.match(self.s, self.i) or self.s[self.i:self.i + 1].isdigit():
            self.fail("malformed number")
        t = m[0]
        if t[:2].lower() == "0x":
            return sign * int(t, 16)
        return sign * (float(t) if any(ch in t for ch in ".eE") else int(t))

    def table(self, depth: int):
        if depth > MAX_DEPTH:
            self.fail(f"tables nested deeper than {MAX_DEPTH}")
        self.expect("{")
        items, n = {}, 0
        while True:
            c = self.peek()
            if c == "}":
                self.i += 1
                break
            if c == "[" and not re.match(r"\[=*\[", self.s[self.i:self.i + 64]):
                self.i += 1
                key = self.value(depth)
                if key is None or isinstance(key, (dict, list)):
                    self.fail("invalid table key")
                self.expect("]")
                self.expect("=")
                items[key] = self.value(depth)
            else:
                m = _NAME.match(self.s, self.i)
                after = m and self.s[m.end():].lstrip(" \t\r\n")
                if m and m[0] not in _KEYWORDS and after.startswith("=") and not after.startswith("=="):
                    self.i = m.end()
                    self.expect("=")
                    items[m[0]] = self.value(depth)
                else:
                    n += 1
                    items[n] = self.value(depth)
            c = self.peek()
            if c == "," or c == ";":
                self.i += 1
            elif c != "}":
                self.fail("expected ',' or '}'")
        items = {(int(k) if isinstance(k, float) and k.is_integer() else k): v
                 for k, v in items.items() if v is not None}
        if all(type(k) is int for k in items) and set(items) == set(range(1, len(items) + 1)):
            return [items[i] for i in range(1, len(items) + 1)]
        return items

    def string(self) -> str:
        s, quote = self.s, self.s[self.i]
        self.i += 1
        out = []
        while True:
            if self.i >= len(s):
                self.fail("unterminated string")
            c = s[self.i]
            if c == quote:
                self.i += 1
                break
            if c == "\n":
                self.fail("unterminated string")
            if c != "\\":
                out.append(ord(c))
                self.i += 1
                continue
            e = s[self.i + 1:self.i + 2]
            self.i += 2
            if e in _ESCAPES:
                out.append(_ESCAPES[e])
            elif e == "\r":
                out.append(10)
                if s[self.i:self.i + 1] == "\n":
                    self.i += 1
            elif e.isdigit():
                m = re.match(r"\d{1,3}", s[self.i - 1:])
                v = int(m[0])
                if v > 255:
                    self.fail("decimal escape too large")
                out.append(v)
                self.i += len(m[0]) - 1
            elif e == "x":
                m = re.match(r"[0-9a-fA-F]{2}", s[self.i:])
                if not m:
                    self.fail("bad \\x escape")
                out.append(int(m[0], 16))
                self.i += 2
            elif e == "z":
                while self.i < len(s) and s[self.i] in " \t\r\n\f\v":
                    self.i += 1
            elif e == "u":
                m = re.match(r"\{([0-9a-fA-F]{1,6})\}", s[self.i:])
                if not m:
                    self.fail("bad \\u escape")
                out.extend(chr(int(m[1], 16)).encode("utf-8", "surrogatepass"))
                self.i += m.end()
            else:
                self.fail(f"bad escape \\{e}")
        return bytes(out).decode("utf-8", "replace")

    def long_string(self) -> str:
        m = re.match(r"\[(=*)\[", self.s[self.i:self.i + 64])
        close = "]" + m[1] + "]"
        start = self.i + m.end()
        end = self.s.find(close, start)
        if end < 0:
            self.fail("unterminated long string")
        self.i = end + len(close)
        body = self.s[start:end]
        if body.startswith("\r\n"):
            body = body[2:]
        elif body.startswith("\n"):
            body = body[1:]
        return body.encode("latin-1").decode("utf-8", "replace")
