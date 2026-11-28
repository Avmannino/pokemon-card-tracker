// Splits a pasted card description into its parts, e.g.
//
//   "1984 Topps Nestlé Dream Team – Dave Winfield #6"
//     -> year 1984, set "Topps Nestlé Dream Team", name "Dave Winfield", number 6
//   "1992 Topps Stadium Club Nolan Ryan – Members Choice #605"
//     -> year 1992, set "Topps Stadium Club", subset "Members Choice",
//        name "Nolan Ryan", number 605
//   "Topps Nestle Dream Team Steve Garvey 1984 #12"
//     -> year 1984, set "Nestle Dream Team", name "Steve Garvey", number 12
//
// The number (#12) and year (a standalone 18xx-20xx) can be anywhere.
// Parts split on dashes with spaces around them (so "O-Pee-Chee" and
// "All-Star" stay whole), en/em dashes and "|". The part naming a card
// maker is the set, a part that reads like a person's name is the player,
// and anything else is the subset. With no separators, the player is the
// two or three name-like words before or after the set. The maker word that
// starts a set ("Topps", "Upper Deck"...) is dropped when more follows, so
// "Topps Nestle Dream Team" is "Nestle Dream Team". Returns only the parts
// it found.

const MAKERS = [
  "topps", "upper deck", "donruss", "fleer", "score", "bowman", "leaf",
  "pinnacle", "panini", "stadium club", "o-pee-chee", "opc", "kellogg",
  "nestlé", "nestle", "sportflics", "classic", "pacific", "skybox", "hoops",
  "collector's choice", "ultra", "select", "prizm", "finest", "chrome",
  "heritage", "gallery", "studio", "triple play", "bazooka", "post",
  "drake's", "pro set", "action packed", "wild card", "playoff", "tiffany",
  "goudey", "play ball", "allen & ginter", "gypsy queen", "archives",
];

const SUBSET_WORDS = [
  "choice", "highlight", "leader", "all-star", "all star", "record",
  "breaker", "rookie", "future stars", "draft", "prospect", "checklist",
  "insert", "traded", "update", "dream team", "world series", "turn back",
  "season", "mvp", "award", "winner", "history", "legends", "classics",
  "heroes", "anniversary", "team", "parallel", "refractor", "glossy",
  "series", "edition", "set", "subset", "special",
];

const SUFFIXES = new Set(["jr", "jr.", "sr", "sr.", "ii", "iii", "iv"]);

// Card makers dropped from the start of a set name when more follows.
const LEADING_MAKERS = ["upper deck", "topps", "donruss", "fleer", "bowman", "panini", "leaf"];

const SEPARATORS = /\s*[–—]\s*|\s+-\s+|\s*\|\s*/;

function includesAny(text, words) {
  const lower = ` ${text.toLowerCase()} `;
  return words.some((word) => lower.includes(word));
}

function isMaker(part) {
  return includesAny(part, MAKERS);
}

function looksLikeName(part) {
  const words = part.split(" ").filter(Boolean);
  const core = words.filter((word) => !SUFFIXES.has(word.toLowerCase().replace(/,$/, "")));
  return (
    core.length >= 2 &&
    core.length <= 3 &&
    !isMaker(part) &&
    !includesAny(part, SUBSET_WORDS) &&
    !/\d/.test(part) &&
    core.every((word) => /^[A-ZÀ-Ý][\p{L}.'’,-]*$/u.test(word))
  );
}

// "Topps Stadium Club Nolan Ryan" -> set "Topps Stadium Club", name
// "Nolan Ryan"; "Steve Garvey Topps Nestle Dream Team" -> name first. Null
// when neither end reads like a name.
function splitName(text) {
  const words = text.split(" ").filter(Boolean);

  for (const size of [3, 2]) {
    if (words.length > size) {
      const trailing = words.slice(-size).join(" ");
      const last = words[words.length - 1].toLowerCase();
      if ((size === 3) === SUFFIXES.has(last) && looksLikeName(trailing)) {
        return { set: words.slice(0, -size).join(" "), name: trailing };
      }
    }
  }

  for (const size of [3, 2]) {
    if (words.length > size) {
      const leading = words.slice(0, size).join(" ");
      const last = words[size - 1].toLowerCase().replace(/,$/, "");
      const rest = words.slice(size).join(" ");
      if ((size === 3) === SUFFIXES.has(last) && looksLikeName(leading) && isMaker(rest)) {
        return { set: rest, name: leading };
      }
    }
  }

  return null;
}

function withoutLeadingMaker(set) {
  const lower = set.toLowerCase();
  for (const maker of LEADING_MAKERS) {
    if (lower.startsWith(`${maker} `)) {
      const rest = set.slice(maker.length).trim();
      // "Topps Big" stays: one word alone isn't a set name.
      return rest.split(" ").length >= 2 ? rest : set;
    }
  }
  return set;
}

export function parseCardLine(text) {
  let rest = (text || "").replace(/\s+/g, " ").trim();
  const result = {};

  const number = rest.match(/#\s*([A-Za-z0-9][A-Za-z0-9-]*)/);
  if (number) {
    result.number = number[1];
    rest = `${rest.slice(0, number.index)} ${rest.slice(number.index + number[0].length)}`;
  }

  rest = rest.replace(/\s+/g, " ").trim();
  const year = rest.match(/(^|\s)((?:18|19|20)\d{2}(?:[-/]\d{2,4})?)(?=\s|$)/);
  if (year) {
    result.year = year[2];
    rest = `${rest.slice(0, year.index)} ${rest.slice(year.index + year[0].length)}`;
  }
  rest = rest.replace(/\s+/g, " ").replace(/\s+([–—|,])/g, " $1");

  rest = rest.replace(/^[\s–—|,:-]+|[\s–—|,:-]+$/g, "");
  const parts = rest.split(SEPARATORS).map((part) => part.trim()).filter(Boolean);

  const nameIndex = parts.findIndex(looksLikeName);
  let name = nameIndex >= 0 ? parts[nameIndex] : null;
  const others = parts.filter((_, index) => index !== nameIndex);

  const setIndex = Math.max(others.findIndex(isMaker), 0);
  let set = others[setIndex] || null;
  const subset = others.filter((_, index) => index !== setIndex);

  if (!name && set) {
    const split = splitName(set);
    if (split) {
      set = split.set;
      name = split.name;
    }
  }

  if (name) result.name = name;
  if (set) result.release_name = withoutLeadingMaker(set);
  if (subset.length) result.set_name = subset.join(" ");
  return result;
}
