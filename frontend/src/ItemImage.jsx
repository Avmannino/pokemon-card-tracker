import { cardTitle, gradeLabel } from "./format.js";
// Your own slab photos by cert number (scripts/slab_photos.py writes it).
import slabPhotos from "./slabPhotos.json";

export function certUrl(certNumber) {
  return `https://www.psacard.com/cert/${certNumber}`;
}

// The photo of a graded item's slab ("front" or "back"): yours when you
// added one for its cert number, else PSA's. Null for raw cards.
export function slabPhoto(item, side = "front") {
  if (item.ownership_grade === "RAW") {
    return null;
  }

  const own = slabPhotos[item.psa_cert_number]?.[side];
  return own ? `${import.meta.env.BASE_URL}${own}` : item.slab_images?.[side] || null;
}

// A collection item's picture. Raw cards show the card art. Graded cards
// show the photo of your slab when there is one, otherwise the card art in
// a drawn slab. `compact` is the small table thumbnail.
function ItemImage({ item, side = "front", compact = false, onClick }) {
  const { card } = item;
  const graded = item.ownership_grade !== "RAW";
  const photo = slabPhoto(item, side);
  const clickable = onClick ? "thumb-clickable" : "";

  if (photo) {
    return (
      <img
        className={`slab-photo ${clickable}`}
        src={photo}
        alt={`${cardTitle(card)}, ${gradeLabel(item.ownership_grade)} slab`}
        loading="lazy"
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
