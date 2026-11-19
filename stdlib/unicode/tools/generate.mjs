// Generate Vaqrin's Unicode 17.0 tables from the official Unicode release.
// Requires Node.js 18+ (built-in fetch); no third-party packages.
import { mkdir, writeFile } from "node:fs/promises";

const VERSION = "17.0.0";
const BASE = "https://www.unicode.org/Public/" + VERSION + "/ucd/";
const SOURCES = {
  UnicodeData: "UnicodeData.txt",
  DerivedCoreProperties: "DerivedCoreProperties.txt",
  DerivedNormalizationProps: "DerivedNormalizationProps.txt",
  PropList: "PropList.txt",
  Scripts: "Scripts.txt",
  PropertyValueAliases: "PropertyValueAliases.txt",
  CaseFolding: "CaseFolding.txt",
  GraphemeBreakProperty: "auxiliary/GraphemeBreakProperty.txt",
  EmojiData: "emoji/emoji-data.txt"
};

async function download(path) {
  const response = await fetch(BASE + path);
  if (!response.ok) throw new Error("Failed to download " + path + ": HTTP " + response.status);
  return response.text();
}
const data = Object.fromEntries(await Promise.all(
  Object.entries(SOURCES).map(async ([name, path]) => [name, await download(path)])
));
for (const name of ["DerivedCoreProperties", "DerivedNormalizationProps", "PropList",
                    "Scripts", "PropertyValueAliases", "CaseFolding",
                    "GraphemeBreakProperty"]) {
  if (!data[name].includes(VERSION)) throw new Error(name + " is not Unicode " + VERSION);
}

const LIMIT = 0x110000;
const CATEGORY_NAMES = ["Cn","Cc","Cf","Cs","Co","Ll","Lm","Lo","Lt","Lu","Mc","Me","Mn",
  "Nd","Nl","No","Pc","Pd","Pe","Pf","Pi","Po","Ps","Sc","Sk","Sm","So","Zl","Zp","Zs"];
const CATEGORY_ID = new Map(CATEGORY_NAMES.map((name, index) => [name, index]));
function rangeOf(value) {
  const parts = value.trim().split("..");
  return [parseInt(parts[0], 16), parseInt(parts[1] || parts[0], 16)];
}
function lines(source) { return source.split(/\r?\n/); }
function fields(line) { return line.split("#")[0].trim().split(";").map(value => value.trim()); }

const category = new Uint8Array(LIMIT);
category.fill(CATEGORY_ID.get("Cn"));
const combining = new Uint8Array(LIMIT);
const lower = new Map(), upper = new Map(), title = new Map(), decompositions = new Map();
let first = null;
for (const line of lines(data.UnicodeData)) {
  if (!line) continue;
  const f = line.split(";");
  const cp = parseInt(f[0], 16);
  if (f[1].endsWith(", First>")) {
    first = { cp, category: f[2], combining: Number(f[3]) };
    continue;
  }
  if (f[1].endsWith(", Last>") && first) {
    for (let n = first.cp; n <= cp; n++) {
      category[n] = CATEGORY_ID.get(first.category);
      combining[n] = first.combining;
    }
    first = null;
    continue;
  }
  category[cp] = CATEGORY_ID.get(f[2]);
  combining[cp] = Number(f[3]);
  if (f[12]) upper.set(cp, parseInt(f[12], 16));
  if (f[13]) lower.set(cp, parseInt(f[13], 16));
  if (f[14]) title.set(cp, parseInt(f[14], 16));
  if (f[5]) {
    const mapping = f[5].trim().split(/\s+/);
    const compatibility = mapping[0].startsWith("<");
    if (compatibility) mapping.shift();
    decompositions.set(cp, { compatibility, values: mapping.map(value => parseInt(value, 16)) });
  }
}

const scriptEntries = [];
for (const line of lines(data.PropertyValueAliases)) {
  const f = fields(line);
  if (f[0] === "sc" && f[1]) scriptEntries.push({ short: f[1], long: f[2], aliases: f.slice(3) });
}
scriptEntries.sort((a, b) => {
  const rank = value => value === "Unknown" ? 0 : value === "Common" ? 1 :
    value === "Inherited" ? 2 : 3;
  return rank(a.long) - rank(b.long) || a.long.localeCompare(b.long);
});
if (scriptEntries.length > 255) throw new Error("Script table exceeds u8 capacity");
const scriptIds = new Map();
scriptEntries.forEach((entry, index) => {
  scriptIds.set(entry.long, index);
  scriptIds.set(entry.short, index);
  entry.aliases.forEach(alias => scriptIds.set(alias, index));
});
const scripts = new Uint8Array(LIMIT);
for (const line of lines(data.Scripts)) {
  const f = fields(line);
  if (!f[0] || !f[1]) continue;
  const [start, end] = rangeOf(f[0]);
  const id = scriptIds.get(f[1]);
  if (id === undefined) throw new Error("Unknown Script value: " + f[1]);
  scripts.fill(id, start, end + 1);
}

const flags = new Uint16Array(LIMIT);
function setProperty(source, property, bit, value = null) {
  for (const line of lines(source)) {
    const f = fields(line);
    if (f[1] !== property || (value !== null && f[2] !== value)) continue;
    const [start, end] = rangeOf(f[0]);
    for (let cp = start; cp <= end; cp++) flags[cp] |= 1 << bit;
  }
}
setProperty(data.DerivedCoreProperties, "Alphabetic", 0);
setProperty(data.DerivedCoreProperties, "Uppercase", 4);
setProperty(data.DerivedCoreProperties, "Lowercase", 5);
setProperty(data.DerivedCoreProperties, "XID_Start", 7);
setProperty(data.DerivedCoreProperties, "XID_Continue", 8);
setProperty(data.PropList, "White_Space", 2);
setProperty(data.EmojiData, "Emoji", 9);
setProperty(data.EmojiData, "Extended_Pictographic", 11);
for (let cp = 0; cp < LIMIT; cp++) {
  const gc = CATEGORY_NAMES[category[cp]];
  if (gc === "Nd") flags[cp] |= 1 << 1;
  if (gc === "Cc") flags[cp] |= 1 << 3;
  if (gc === "Lt") flags[cp] |= 1 << 6;
  if (gc === "Mn" || gc === "Mc" || gc === "Me") flags[cp] |= 1 << 10;
}

const GCB_NAMES = ["Other","CR","LF","Control","Extend","ZWJ","Regional_Indicator","Prepend",
  "SpacingMark","L","V","T","LV","LVT"];
const GCB_IDS = new Map(GCB_NAMES.map((name, index) => [name, index]));
const grapheme = new Uint8Array(LIMIT);
for (const line of lines(data.GraphemeBreakProperty)) {
  const f = fields(line);
  if (!f[0] || !f[1]) continue;
  const [start, end] = rangeOf(f[0]);
  const id = GCB_IDS.get(f[1]);
  if (id === undefined) throw new Error("Unknown Grapheme_Cluster_Break value: " + f[1]);
  grapheme.fill(id, start, end + 1);
}

const INCB_NAMES = ["None","Consonant","Extend","Linker"];
const INCB_IDS = new Map(INCB_NAMES.map((name, index) => [name, index]));
const indic = new Uint8Array(LIMIT);
for (const line of lines(data.DerivedCoreProperties)) {
  const f = fields(line);
  if (f[1] !== "InCB") continue;
  const [start, end] = rangeOf(f[0]);
  const id = INCB_IDS.get(f[2]);
  if (id === undefined) throw new Error("Unknown Indic_Conjunct_Break value: " + f[2]);
  indic.fill(id, start, end + 1);
}

const exclusions = new Uint8Array(LIMIT);
for (const line of lines(data.DerivedNormalizationProps)) {
  const f = fields(line);
  if (f[1] !== "Full_Composition_Exclusion") continue;
  const [start, end] = rangeOf(f[0]);
  exclusions.fill(1, start, end + 1);
}

const simpleCases = [];
for (const cp of [...new Set([...lower.keys(), ...upper.keys(), ...title.keys()])].sort((a,b) => a-b)) {
  simpleCases.push(cp, lower.get(cp) ?? cp, upper.get(cp) ?? cp, title.get(cp) ?? cp);
}
const decompositionIndex = [], decompositionScalars = [];
for (const [cp, entry] of [...decompositions.entries()].sort((a,b) => a[0]-b[0])) {
  const offset = decompositionScalars.length;
  decompositionScalars.push(...entry.values);
  decompositionIndex.push(cp, offset, entry.values.length | (entry.compatibility ? 0x100 : 0));
}
const compositionPairs = [];
for (const [cp, entry] of decompositions) {
  if (!entry.compatibility && entry.values.length === 2 && !exclusions[cp]) {
    compositionPairs.push([entry.values[0], entry.values[1], cp]);
  }
}
compositionPairs.sort((a,b) => a[0]-b[0] || a[1]-b[1]);

const folds = new Map();
for (const line of lines(data.CaseFolding)) {
  const f = fields(line);
  if (!f[0] || (f[1] !== "C" && f[1] !== "F")) continue;
  const cp = parseInt(f[0], 16);
  const mapping = f[2].split(/\s+/).map(value => parseInt(value, 16));
  if (!folds.has(cp) || f[1] === "F") folds.set(cp, mapping);
}
const caseFoldIndex = [], caseFoldScalars = [];
for (const [cp, mapping] of [...folds.entries()].sort((a,b) => a[0]-b[0])) {
  const offset = caseFoldScalars.length;
  caseFoldScalars.push(...mapping);
  caseFoldIndex.push(cp, offset, mapping.length);
}

function pack(cp) {
  return BigInt(category[cp]) |
    (BigInt(scripts[cp]) << 5n) |
    (BigInt(combining[cp]) << 13n) |
    (BigInt(flags[cp]) << 21n) |
    (BigInt(grapheme[cp]) << 34n) |
    (BigInt(indic[cp]) << 38n);
}
const propertyRanges = [];
let rangeStart = 0, previous = pack(0);
for (let cp = 1; cp <= LIMIT; cp++) {
  const current = cp === LIMIT ? -1n : pack(cp);
  if (cp === LIMIT || current !== previous) {
    propertyRanges.push((BigInt(rangeStart) << 21n) | BigInt(cp - 1), previous);
    rangeStart = cp;
    previous = current;
  }
}
const hex = value => "0x" + value.toString(16);
const u32 = values => values.map(value => hex(value) + "u32").join(",\n    ");
const u64 = values => values.map(value => hex(value) + "u64").join(",\n    ");
const dataSource = [
  "package unicode",
  "",
  "// Generated from Unicode Character Database " + VERSION + ".",
  "// Source: https://www.unicode.org/Public/" + VERSION + "/ucd/",
  "// Do not edit by hand. Regenerate with tools/generate.mjs.",
  "",
  "pub const unicodePropertyRanges = [_]u64{", "    " + u64(propertyRanges), "};", "",
  "pub const simpleCaseMappings = [_]u32{", "    " + u32(simpleCases), "};", "",
  "pub const decompositionIndex = [_]u32{", "    " + u32(decompositionIndex), "};", "",
  "pub const decompositionScalars = [_]u32{", "    " + u32(decompositionScalars), "};", "",
  "pub const compositionPairs = [_]u32{", "    " + u32(compositionPairs.flat()), "};", "",
  "pub const caseFoldIndex = [_]u32{", "    " + u32(caseFoldIndex), "};", "",
  "pub const caseFoldScalars = [_]u32{", "    " + u32(caseFoldScalars), "};", ""
].join("\n");
const pascal = value => value.split("_").map(part =>
  part ? part[0].toUpperCase() + part.slice(1) : "").join("");
const scriptNames = scriptEntries.map(entry => pascal(entry.long));
if (new Set(scriptNames).size !== scriptNames.length) throw new Error("Script enum name collision");
const scriptSource = [
  "package unicode", "",
  "// Generated from PropertyValueAliases.txt in Unicode " + VERSION + ".",
  "// Do not edit by hand. Regenerate with tools/generate.mjs.", "",
  "pub enum Script {", "    " + scriptNames.join(",\n    "), "}", "",
  "pub func scriptFromId(value: u8) Script {", "    switch value {",
  ...scriptNames.map((name,index) => "        " + index + " => Script." + name + ","),
  "        else => Script.Unknown,", "    }", "}", ""
].join("\n");
const out = new URL("../", import.meta.url);
await mkdir(out, { recursive: true });
await writeFile(new URL("unicode_data.vqr", out), dataSource);
await writeFile(new URL("script.vqr", out), scriptSource);
process.stdout.write("Generated Unicode " + VERSION + ": " + (propertyRanges.length / 2) +
  " property ranges, " + decompositions.size + " decompositions, " +
  compositionPairs.length + " composition pairs, " + folds.size + " case folds.\n");
