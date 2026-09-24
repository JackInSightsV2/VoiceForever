// Read a WoW SavedVariables file as data: a TypeScript port of pipeline/src/vo/savedvars.py.
//
// SavedVariables are Lua source (`Name = { ["key"] = value, ... }`), but uploads are untrusted, so nothing is
// executed: this parses the data subset the client writes (assignments of nil, booleans, numbers, strings and
// nested tables) and rejects anything else, such as a function call or an expression. Keep the rules in step with
// savedvars.py: a file this accepts must be one `vo ingest` accepts.

export const MAX_DEPTH = 32;

export class Malformed extends Error {}

/** A Lua table: a list when its keys are exactly 1..n, otherwise a Map (never a plain object: no prototype keys). */
export type LuaValue = null | boolean | number | string | LuaValue[] | LuaTable;
export type LuaTable = Map<string | number | boolean, LuaValue>;

const NAME = /[A-Za-z_][A-Za-z0-9_]*/y;
const NUMBER = /0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/y;
const LONG_OPEN = /\[(=*)\[/y;
const LONG_COMMENT = /--\[(=*)\[/y;
const ESCAPES: Record<string, number> = {
  a: 7, b: 8, f: 12, n: 10, r: 13, t: 9, v: 11, "\\": 92, '"': 34, "'": 39, "\n": 10,
};
const KEYWORDS: Record<string, null | boolean> = { true: true, false: false, nil: null };
const SPACE = " \t\r\n\f\v";
const utf8 = new TextDecoder("utf-8", { fatal: false });

/** The file's top-level assignments; tables become arrays (keys 1..n) or Maps. Throws Malformed. */
export function loads(data: Uint8Array): Map<string, LuaValue> {
  if (data[0] === 0xef && data[1] === 0xbb && data[2] === 0xbf) data = data.subarray(3);
  return new Parser(latin1(data)).file();
}

// Bytes to a string one char per byte (TextDecoder's "latin1" is windows-1252, which isn't 1:1).
function latin1(data: Uint8Array): string {
  let out = "";
  for (let i = 0; i < data.length; i += 0x8000) {
    out += String.fromCharCode(...data.subarray(i, i + 0x8000));
  }
  return out;
}

function matchAt(re: RegExp, s: string, i: number): RegExpExecArray | null {
  re.lastIndex = i;
  return re.exec(s);
}

const isKeyword = (w: string) => Object.hasOwn(KEYWORDS, w);

class Parser {
  i = 0;
  constructor(private s: string) {}

  fail(msg: string): never {
    let line = 1;
    for (let j = 0; j < this.i && j < this.s.length; j++) if (this.s.charCodeAt(j) === 10) line++;
    throw new Malformed(`line ${line}: ${msg}`);
  }

  skip() {
    const s = this.s;
    while (this.i < s.length) {
      const c = s[this.i];
      if (SPACE.includes(c)) {
        this.i++;
      } else if (s.startsWith("--", this.i)) {
        const long = matchAt(LONG_COMMENT, s, this.i);
        if (long) {
          const end = s.indexOf("]" + long[1] + "]", this.i);
          if (end < 0) this.fail("unterminated comment");
          this.i = end + long[1].length + 2;
        } else {
          const end = s.indexOf("\n", this.i);
          this.i = end < 0 ? s.length : end + 1;
        }
      } else {
        return;
      }
    }
  }

  peek(): string {
    this.skip();
    return this.s.slice(this.i, this.i + 1);
  }

  expect(c: string) {
    if (this.peek() !== c) this.fail(`expected '${c}', got ${JSON.stringify(this.s.slice(this.i, this.i + 10))}`);
    this.i++;
  }

  file(): Map<string, LuaValue> {
    const out = new Map<string, LuaValue>();
    while (this.peek()) {
      const m = matchAt(NAME, this.s, this.i);
      if (!m || isKeyword(m[0])) this.fail("expected a variable assignment");
      this.i += m[0].length;
      this.expect("=");
      out.set(m[0], this.value(0));
      if (this.peek() === ";") this.i++;
    }
    return out;
  }

  value(depth: number): LuaValue {
    const c = this.peek();
    if (!c) this.fail("unexpected end of file");
    if (c === "{") return this.table(depth + 1);
    if (c === '"' || c === "'") return this.string();
    if (c === "[" && matchAt(LONG_OPEN, this.s, this.i)) return this.longString();
    const name = matchAt(NAME, this.s, this.i);
    if (name) {
      if (!isKeyword(name[0])) this.fail(`unexpected '${name[0]}': only data is allowed`);
      this.i += name[0].length;
      return KEYWORDS[name[0]];
    }
    let sign = 1;
    if (c === "-") {
      sign = -1;
      this.i++;
    }
    const m = matchAt(NUMBER, this.s, this.i);
    if (!m) this.fail(`unexpected ${JSON.stringify(this.s.slice(this.i, this.i + 10))}`);
    this.i += m[0].length;
    if (matchAt(NAME, this.s, this.i) || /[0-9]/.test(this.s[this.i] ?? "")) this.fail("malformed number");
    const t = m[0];
    return sign * (t.slice(0, 2).toLowerCase() === "0x" ? parseInt(t, 16) : Number(t));
  }

  table(depth: number): LuaValue[] | LuaTable {
    if (depth > MAX_DEPTH) this.fail(`tables nested deeper than ${MAX_DEPTH}`);
    this.expect("{");
    const items: LuaTable = new Map();
    let n = 0;
    for (;;) {
      let c = this.peek();
      if (c === "}") {
        this.i++;
        break;
      }
      if (c === "[" && !matchAt(LONG_OPEN, this.s, this.i)) {
        this.i++;
        const key = this.value(depth);
        if (key === null || typeof key === "object") this.fail("invalid table key");
        this.expect("]");
        this.expect("=");
        items.set(key, this.value(depth));
      } else {
        const m = matchAt(NAME, this.s, this.i);
        let j = m ? this.i + m[0].length : 0;
        while (m && j < this.s.length && " \t\r\n".includes(this.s[j])) j++;
        if (m && !isKeyword(m[0]) && this.s[j] === "=" && this.s[j + 1] !== "=") {
          this.i += m[0].length;
          this.expect("=");
          items.set(m[0], this.value(depth));
        } else {
          items.set(++n, this.value(depth));
        }
      }
      c = this.peek();
      if (c === "," || c === ";") this.i++;
      else if (c !== "}") this.fail("expected ',' or '}'");
    }
    for (const [k, v] of items) if (v === null) items.delete(k);
    const size = items.size;
    let list = true;
    for (let k = 1; k <= size && list; k++) list = items.has(k);
    return list ? Array.from({ length: size }, (_, k) => items.get(k + 1)!) : items;
  }

  string(): string {
    const s = this.s;
    const quote = s[this.i++];
    const out: number[] = [];
    for (;;) {
      if (this.i >= s.length) this.fail("unterminated string");
      const c = s[this.i];
      if (c === quote) {
        this.i++;
        break;
      }
      if (c === "\n") this.fail("unterminated string");
      if (c !== "\\") {
        out.push(c.charCodeAt(0));
        this.i++;
        continue;
      }
      const e = s.slice(this.i + 1, this.i + 2);
      this.i += 2;
      if (Object.hasOwn(ESCAPES, e)) {
        out.push(ESCAPES[e]);
      } else if (e === "\r") {
        out.push(10);
        if (s[this.i] === "\n") this.i++;
      } else if (/[0-9]/.test(e)) {
        const m = /^[0-9]{1,3}/.exec(s.slice(this.i - 1, this.i + 2))!;
        const v = Number(m[0]);
        if (v > 255) this.fail("decimal escape too large");
        out.push(v);
        this.i += m[0].length - 1;
      } else if (e === "x") {
        const m = /^[0-9a-fA-F]{2}/.exec(s.slice(this.i, this.i + 2));
        if (!m) this.fail("bad \\x escape");
        out.push(parseInt(m[0], 16));
        this.i += 2;
      } else if (e === "z") {
        while (this.i < s.length && SPACE.includes(s[this.i])) this.i++;
      } else if (e === "u") {
        const m = /^\{([0-9a-fA-F]{1,6})\}/.exec(s.slice(this.i, this.i + 8));
        if (!m || parseInt(m[1], 16) > 0x10ffff) this.fail("bad \\u escape");
        out.push(...utf8Encode(parseInt(m[1], 16)));
        this.i += m[0].length;
      } else {
        this.fail(`bad escape \\${e}`);
      }
    }
    return utf8.decode(new Uint8Array(out));
  }

  longString(): string {
    const m = matchAt(LONG_OPEN, this.s, this.i)!;
    const close = "]" + m[1] + "]";
    const start = this.i + m[0].length;
    const end = this.s.indexOf(close, start);
    if (end < 0) this.fail("unterminated long string");
    this.i = end + close.length;
    let body = this.s.slice(start, end);
    if (body.startsWith("\r\n")) body = body.slice(2);
    else if (body.startsWith("\n")) body = body.slice(1);
    return utf8.decode(Uint8Array.from(body, (ch) => ch.charCodeAt(0)));
  }
}

// UTF-8 of a code point (surrogates included, as Python's surrogatepass).
function utf8Encode(cp: number): number[] {
  if (cp < 0x80) return [cp];
  if (cp < 0x800) return [0xc0 | (cp >> 6), 0x80 | (cp & 63)];
  if (cp < 0x10000) return [0xe0 | (cp >> 12), 0x80 | ((cp >> 6) & 63), 0x80 | (cp & 63)];
  return [0xf0 | (cp >> 18), 0x80 | ((cp >> 12) & 63), 0x80 | ((cp >> 6) & 63), 0x80 | (cp & 63)];
}
