import { useEffect, useState } from "react";

import { cardTitle, gradeLabel, money } from "./format.js";

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
                {gradeLabel(card.owned.grade)} · value per card
              </span>
            </div>
          )}
        </figcaption>
      </figure>
    </div>
  );
}

export default CardZoomModal;
