import { cardTitle, gradeLabel } from "./format.js";

export function certUrl(certNumber) {
  return `https://www.psacard.com/cert/${certNumber}`;
}

// A collection item's picture. Raw cards show the card art. Graded cards
// show PSA's photo of your slab when there is one, otherwise the card art
// in a drawn slab. `compact` is the small table thumbnail.
function ItemImage({ item, side = "front", compact = false, onClick }) {
  const { card } = item;
  const graded = item.ownership_grade !== "RAW";
  const photo = graded ? item.slab_images?.[side] : null;
  const clickable = onClick ? "thumb-clickable" : "";

  if (photo) {
    return (
      <img
        className={`slab-photo ${clickable}`}
        src={photo}
        alt={`${cardTitle(card)}, ${gradeLabel(item.ownership_grade)} slab`}
        onClick={onClick}
      />
    );
  }

  if (!card.image_url) {
    return null;
  }

  if (!graded) {
    return (
      <img
        className={clickable}
        src={card.image_url}
        alt={cardTitle(card)}
        onClick={onClick}
      />
    );
  }

  return (
    <div
      className={`drawn-slab ${compact ? "compact" : ""} ${clickable}`}
      role="img"
      aria-label={`${cardTitle(card)}, ${gradeLabel(item.ownership_grade)}`}
      onClick={onClick}
    >
      <div className="drawn-slab-label">
        {!compact && (
          <span className="drawn-slab-name">
            {card.name}
            {card.card_number ? ` #${card.card_number}` : ""}
          </span>
        )}

        <strong>
          {compact
            ? item.ownership_grade.replace("PSA_", "")
            : gradeLabel(item.ownership_grade)}
        </strong>
      </div>

      <div className="drawn-slab-window">
        <img src={card.image_url} alt="" />
      </div>
    </div>
  );
}

export default ItemImage;
