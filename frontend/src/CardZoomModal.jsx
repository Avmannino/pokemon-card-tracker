import { useEffect, useState } from "react";

import { cardTitle, gradeLabel, money, variantLabel } from "./format.js";
import ItemImage, { certUrl, slabPhoto } from "./ItemImage.jsx";

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

function signedMoney(value) {
  if (value === null || value === undefined) {
    return "—";
  }
  return `${value < 0 ? "−" : "+"}${money(Math.abs(value))}`;
}

// A raw card's grading outlook (from Grading Opportunities): what each grade
// is worth, and what grading would net at PSA 9 and PSA 10.
function GradingOutcomes({ grading }) {
  const outcomeTone = (value) =>
    value === null || value === undefined ? "" : value >= 0 ? "positive" : "negative";

  return (
    <div className="grading-outcomes">
      <div className="grading-outcomes-row">
        <span>Raw market</span>
        <strong>{money(grading.raw_value)}</strong>
      </div>
      <div className="grading-outcomes-row">
        <span>PSA 9</span>
        <strong>{money(grading.psa9_value)}</strong>
      </div>
      <div className="grading-outcomes-row">
        <span>PSA 10</span>
        <strong>{money(grading.psa10_value)}</strong>
      </div>
      <div className="grading-outcomes-row">
        <span>Grading cost</span>
        <strong>{money(grading.grading_cost)}</strong>
      </div>

      {grading.available ? (
        <>
          <p className="eyebrow">POTENTIAL OUTCOMES</p>
          <div className="grading-outcomes-row">
            <span>PSA 9</span>
            <strong className={outcomeTone(grading.psa9_net)}>
              {grading.psa9_net === null ? "PSA 9 price unknown" : `${signedMoney(grading.psa9_net)} net`}
            </strong>
          </div>
          <div className="grading-outcomes-row">
            <span>PSA 10</span>
            <strong className={outcomeTone(grading.psa10_net)}>
              {signedMoney(grading.psa10_net)} net
            </strong>
          </div>
          <p className="grading-outcomes-note">
            {grading.label} · score {grading.score}/100. {grading.risk_note}
          </p>
        </>
      ) : (
        <p className="grading-outcomes-note">{grading.unavailable_reason}</p>
      )}

      {grading.graded_source && (
        <p className="grading-outcomes-note">
          PSA 10: {grading.graded_source}
          {shortDate(grading.graded_as_of) ? ` As of ${shortDate(grading.graded_as_of)}.` : ""}
        </p>
      )}

      <p className="grading-outcomes-note">
        Market opportunity only. Actual grade depends on card condition.
      </p>
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
  const [side, setSide] = useState("front");

  useEffect(() => {
    setSide("front");
  }, [card]);

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

  // Collection items (graded ones may have a slab photo); search results
  // only have the card.
  const item = card.owned?.item;

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

        {item ? (
          <ItemImage item={item} side={side} />
        ) : (
          <img src={card.image_url} alt={cardTitle(card)} />
        )}

        {item && slabPhoto(item, "back") && (
          <button
            type="button"
            className="text-button"
            onClick={() => setSide(side === "front" ? "back" : "front")}
          >
            {side === "front" ? "Show back" : "Show front"}
          </button>
        )}

        <figcaption>
          <strong>{cardTitle(card)}</strong>
          {card.set_name && <span>{card.set_name}</span>}

          {item?.psa_cert_number && (
            <a
              className="manual-source-link"
              href={certUrl(item.psa_cert_number)}
              target="_blank"
              rel="noreferrer"
            >
              PSA cert #{item.psa_cert_number}
            </a>
          )}

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

              {card.owned.grading && <GradingOutcomes grading={card.owned.grading} />}
            </div>
          )}
        </figcaption>
      </figure>
    </div>
  );
}

export default CardZoomModal;
