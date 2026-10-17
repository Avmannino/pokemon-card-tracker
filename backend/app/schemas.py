from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


Grade = Literal["RAW", "PSA_7", "PSA_8", "PSA_9", "PSA_10"]


class SearchCard(BaseModel):
    poketrace_id: str
    name: str
    card_number: str | None = None
    set_name: str | None = None
    set_slug: str | None = None
    variant: str | None = None
    rarity: str | None = None
    image_url: str | None = None
    tcgplayer_id: str | None = None
    marketplace_urls: dict[str, str | None] = Field(default_factory=dict)


class VariantOption(BaseModel):
    poketrace_id: str
    variant: str | None = None
    image_url: str | None = None
    rarity: str | None = None
    raw_market_estimate: float | None = None


class AddCollectionRequest(BaseModel):
    card: SearchCard
    quantity: int = Field(default=1, ge=1, le=100)
    ownership_grade: Grade = "RAW"
    purchase_price: float | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=2000)
    # The add form shows the variant prominently and asks you to check it,
    # so a card added through it counts as variant-confirmed.
    variant_confirmed: bool = False
    # Other variants of the same printing seen in the search results.
    variant_siblings: list[VariantOption] | None = None


class VariantChangeRequest(BaseModel):
    # PokeTrace id of the variant you actually own; the card's current id
    # just confirms it.
    poketrace_id: str = Field(min_length=8, max_length=64)


class GradedValuesRequest(BaseModel):
    source: str = Field(min_length=2, max_length=120)
    source_url: str | None = None
    psa_7: float | None = Field(default=None, ge=0)
    psa_8: float | None = Field(default=None, ge=0)
    psa_9: float | None = Field(default=None, ge=0)
    psa_10: float | None = Field(default=None, ge=0)
