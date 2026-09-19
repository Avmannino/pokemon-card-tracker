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
