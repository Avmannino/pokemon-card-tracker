export function money(value) {
  if (
    value === null ||
    value === undefined ||
    Number.isNaN(Number(value))
  ) {
    return "—";
  }

  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(Number(value));
}

export function gradeLabel(grade) {
  if (grade === "RAW") {
    return "Raw";
  }

  return grade.replace("_", " ");
}

// PokeTrace variant codes -> what's printed on the card / how collectors say it.
const VARIANT_LABELS = {
  Normal: "Normal",
  Holofoil: "Holo",
  Reverse_Holofoil: "Reverse Holo",
  "1st_Edition": "1st Edition",
  "1st_Edition_Holofoil": "1st Edition Holo",
  Unlimited: "Unlimited",
};

export function variantLabel(variant) {
  if (!variant) {
    return null;
  }

  return VARIANT_LABELS[variant] || variant.replaceAll("_", " ");
}

// Groups search results that are variants of one printing (same set, number
// and TCGPlayer product), so a result can say "also comes as Holo".
export function printingKey(card) {
  if (!card?.tcgplayer_id) {
    return null;
  }

  return [card.tcgplayer_id, card.card_number || "", card.set_slug || ""].join("|");
}

export function percent(value) {
  if (
    value === null ||
    value === undefined ||
    Number.isNaN(Number(value))
  ) {
    return "—";
  }

  const sign = value > 0 ? "+" : "";
  return `${sign}${Number(value).toFixed(2)}%`;
}

export function cardTitle(card) {
  if (!card) {
    return "";
  }

  return card.card_number
    ? `${card.name} #${card.card_number}`
    : card.name;
}
