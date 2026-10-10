import { useEffect, useState } from "react";

import { cardTitle, gradeLabel, money, variantLabel } from "./format.js";

const CONFIDENCE_LABELS = {
  HIGH: "High confidence",
  MEDIUM: "Medium confidence",
  LOW: "Low confidence",
  MANUAL: "Your manual price",
  NONE: "No price",
};

const ROLE_LABELS = {
  primary: "Used",
  supporting: "Blended in",
  excluded: "Not used",
};

function shortDate(value) {
  if (!value) {
    return null;
  }

  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? null
    : date.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

// Why a value is what it is: the method, and every source with its role.
function PriceDetails({ valuation }) {
  if (!valuation) {
    return null;
  }

  return (
    <div className="price-details">
      <p className="price-details-summary">{valuation.summary}</p>

      <p className="price-details-meta">
        {CONFIDENCE_LABELS[valuation.confidence] || valuation.confidence}
        {valuation.divergent ? " · sources disagree" : ""}
        {shortDate(valuation.last_updated)
          ? ` · updated ${shortDate(valuation.last_updated)}`
          : ""}
      </p>

      {valuation.components?.length > 0 && (
        <ul className="price-details-sources">
          {valuation.components.map((component, index) => (
            <li
              key={`${component.source}-${index}`}
              className={`source-${component.role}`}
            >
              <div className="source-line">
                <span className="source-name">
                  {component.source_url ? (
                    <a href={component.source_url} target="_blank" rel="noreferrer">
                      {component.label}
                    </a>
                  ) : (
                    component.label
                  )}
                </span>

                <strong>{money(component.value)}</strong>
              </div>

              <div className="source-sub">
                {ROLE_LABELS[component.role] || component.role}
                {component.role === "supporting" && component.weight !== null
                  ? ` (${Math.round(component.weight * 100)}%)`
                  : ""}
                {component.sale_count
                  ? ` · ${Number(component.sale_count).toLocaleString()} sales`
                  : ""}
                {shortDate(component.as_of) ? ` · ${shortDate(component.as_of)}` : ""}
                {component.reason ? ` · ${component.reason}` : ""}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function useCardZoom() {
  const [zoomedCard, setZoomedCard] = useState(null);

  return {
    zoomedCard,
    // `owned` ({ grade, valueEach }) is passed for cards in the collection so
    // the modal can show their value per card; search results omit it.
    openZoom: (card, owned) => {
      if (card?.image_url) {
        setZoomedCard({ ...card, owned });
      }
    },
    closeZoom: () => setZoomedCard(null),
  };
}

function CardZoomModal({ card, onClose }) {
  useEffect(() => {
    if (!card) {
      return undefined;
    }

    function handleKeyDown(event) {
      if (event.key === "Escape") {
        onClose();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [card, onClose]);

  if (!card) {
    return null;
  }

  return (
    <div className="modal-backdrop card-zoom-backdrop" onClick={onClose}>
      <figure
        className="card-zoom"
        onClick={(event) => event.stopPropagation()}
      >
        <button
          type="button"
          className="card-zoom-close"
          onClick={onClose}
        >
          ×
        </button>

        <img src={card.image_url} alt={cardTitle(card)} />

        <figcaption>
          <strong>{cardTitle(card)}</strong>
          {card.set_name && <span>{card.set_name}</span>}

          {card.owned && (
            <div className="card-zoom-price">
              <strong>{money(card.owned.valueEach)}</strong>
              <span>
                {gradeLabel(card.owned.grade)}
                {variantLabel(card.owned.variant)
                  ? ` · ${variantLabel(card.owned.variant)}`
                  : ""}{" "}
                · value per card
              </span>

              <PriceDetails valuation={card.owned.valuation} />
            </div>
          )}
        </figcaption>
      </figure>
    </div>
  );
}

export default CardZoomModal;
