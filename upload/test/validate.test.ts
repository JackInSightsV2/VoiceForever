import { expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { loads, Malformed } from "../src/savedvars";
import { Rejected, validate } from "../src/validate";

export const FIXTURE = join(import.meta.dir, "..", "..", "pipeline", "tests", "fixtures", "VoiceForever.lua");
const enc = (s: string) => new TextEncoder().encode(s);

test("the pipeline's fixture is valid", () => {
  expect(validate(readFileSync(FIXTURE))).toEqual({ records: 9, valid: 9, locales: { enUS: 8, deDE: 1 } });
});

test("parser reads Lua data like savedvars.py", () => {
  const got = loads(enc('-- header\nA = {\n\t["s"] = "a\\"b\\\\c\\n\\065\\xe2\\x9c\\x93\\u{263A}",\n'
    + '\t["n"] = -1.5e2, ["h"] = 0x10, ["t"] = true, ["f"] = false, ["z"] = nil,\n'
    + '\t[3] = "three", key = [[long\nstring]],\n'
    + '\t{ 1, 2, }, -- [1]\n} --[[ block\ncomment ]]\nB = { "x", "y"; "z" }\nC = 7\n'));
  expect(got.get("A")).toEqual(new Map<string | number, unknown>([
    ["s", 'a"b\\c\nA✓☺'], ["n", -150], ["h", 16], ["t", true], ["f", false],
    [3, "three"], ["key", "long\nstring"], [1, [1, 2]],
  ]) as never);
  expect(got.get("B")).toEqual(["x", "y", "z"]);
  expect(got.get("C")).toBe(7);
});

test("a sparse capture table keeps its numbered records", () => {
  const r = '{ kind = "miss", event = "GOSSIP_SHOW", text = "hi", hash = "0000abcd", locale = "deDE" }';
  expect(validate(enc(`VoiceForeverDB = { capture = { [2] = ${r}, [5] = ${r}, note = "x" } }`)))
    .toEqual({ records: 2, valid: 2, locales: { deDE: 2 } });
});

const MALFORMED: [string, string][] = [
  ['VoiceForeverDB = os.execute("touch /tmp/pwned")', "only data is allowed"],
  ["VoiceForeverDB = loadstring('x')()", "only data is allowed"],
  ['VoiceForeverDB = { capture = { require("x") } }', "only data is allowed"],
  ["VoiceForeverDB = { capture = { function() end } }", "only data is allowed"],
  ["VoiceForeverDB = { capture = { [os.time()] = 1 } }", "only data is allowed"],
  ["VoiceForeverDB = { capture = 1 + 2 }", "expected ',' or '}'"],
  ['VoiceForeverDB = { capture = { "a" .. "b" } }', "expected ',' or '}'"],
  ["VoiceForeverDB = { capture = { x = y } }", "only data is allowed"],
  ["VoiceForeverDB = {} os.exit()", "expected '='"],
  ["print('hi')", "expected '='"],
  ["VoiceForeverDB = { capture = { { kind = 'miss' }", "expected"],
  ['VoiceForeverDB = { capture = { "unterminated } }', "unterminated string"],
  ["VoiceForeverDB = ", "unexpected end of file"],
  ["VoiceForeverDB = " + "{".repeat(40) + "}".repeat(40), "nested deeper"],
  ["VoiceForeverDB = { capture = { 12abc } }", "malformed number"],
  ['VoiceForeverDB = { capture = { "\\q" } }', "bad escape"],
];

for (const [body, error] of MALFORMED) {
  test(`rejects ${JSON.stringify(body.slice(0, 50))}`, () => {
    expect(() => validate(enc(body))).toThrow(error);
    expect(() => validate(enc(body))).toThrow(Rejected);
  });
}

test("parser errors are Malformed with a line number", () => {
  expect(() => loads(enc("A = 1\nB = os.time()"))).toThrow(new Malformed("line 2: unexpected 'os': only data is allowed"));
});

test("wrong top-level table or no capture list is rejected", () => {
  expect(() => validate(enc("OtherAddonDB = { capture = {} }"))).toThrow("No VoiceForeverDB.capture");
  expect(() => validate(enc("VoiceForeverDB = { settings = {} }"))).toThrow("No VoiceForeverDB.capture");
  expect(() => validate(enc("VoiceForeverDB = 5"))).toThrow("No VoiceForeverDB.capture");
  expect(() => validate(enc("VoiceForeverDB = { capture = 5 }"))).toThrow("isn't a table");
});

test("a capture list without Capture records is rejected", () => {
  expect(() => validate(enc("VoiceForeverDB = { capture = {} }"))).toThrow("no Capture records");
  expect(() => validate(enc('VoiceForeverDB = { capture = { { kind = "miss" }, 1, "x" } }'))).toThrow("no Capture records");
});

test("oversized and empty files are rejected", () => {
  const big = enc("VoiceForeverDB = { capture = {} }" + " ".repeat(100));
  expect(() => validate(big, 100)).toThrow("too large");
  try {
    validate(big, 100);
  } catch (e) {
    expect((e as Rejected).status).toBe(413);
  }
  expect(() => validate(new Uint8Array())).toThrow("empty");
});

test("a UTF-8 BOM is accepted", () => {
  const data = readFileSync(FIXTURE);
  expect(validate(Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), data])).valid).toBe(9);
});
