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
    # Added from the Small Hits drawer: kept out of the main table and the
    # regular syncs (see price_sync).
    small_hit: bool = False


class QuantityRequest(BaseModel):
    # How many copies you hold now (remove the card to go to zero).
    quantity: int = Field(ge=1, le=999)


class PriceChartingLinkRequest(BaseModel):
    # A raw card's PriceCharting page; empty removes the link.
    url: str | None = Field(default=None, max_length=500)


class VariantChangeRequest(BaseModel):
    # PokeTrace id of the variant you actually own; the card's current id
    # just confirms it.
    poketrace_id: str = Field(min_length=8, max_length=64)


SportsGrade = Literal["RAW", "PSA_7", "PSA_8", "PSA_9", "PSA_10"]
CARDSIGHT_ID = r"^[0-9a-fA-F-]{36}$"


class SportsCardRef(BaseModel):
    # A card from the sports search (cardsight.normalize_card).
    cardsight_id: str = Field(pattern=CARDSIGHT_ID)
    name: str = Field(min_length=1, max_length=200)
    number: str | None = Field(default=None, max_length=40)
    year: str | None = Field(default=None, max_length=10)
    release_name: str | None = Field(default=None, max_length=200)
    set_name: str | None = Field(default=None, max_length=200)
    attributes: list[str] = Field(default_factory=list, max_length=20)


class SportsAddRequest(BaseModel):
    card: SportsCardRef
    quantity: int = Field(default=1, ge=1, le=999)
    ownership_grade: SportsGrade = "RAW"
    notes: str | None = Field(default=None, max_length=2000)
    manual_value: float | None = Field(default=None, ge=0)
    manual_source_url: str | None = Field(default=None, max_length=500)


class SportsManualAddRequest(BaseModel):
    # A card CardSight doesn't have, entered by hand with your own value.
    name: str = Field(min_length=1, max_length=200)
    year: str | None = Field(default=None, max_length=10)
    release_name: str | None = Field(default=None, max_length=200)
    set_name: str | None = Field(default=None, max_length=200)
    number: str | None = Field(default=None, max_length=40)
    league: Literal["MLB", "NFL", "NBA", "NHL"] | None = None
    quantity: int = Field(default=1, ge=1, le=999)
    ownership_grade: SportsGrade = "RAW"
    manual_value: float = Field(ge=0)
    manual_source_url: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)


class SportsCardDetails(BaseModel):
    # A hand-added card's details (CardSight cards keep CardSight's).
    name: str = Field(min_length=1, max_length=200)
    year: str | None = Field(default=None, max_length=10)
    release_name: str | None = Field(default=None, max_length=200)
    set_name: str | None = Field(default=None, max_length=200)
    number: str | None = Field(default=None, max_length=40)
    league: Literal["MLB", "NFL", "NBA", "NHL"] | None = None


class SportsItemUpdate(BaseModel):
    # Only the fields sent are changed; manual_value null clears your value.
    quantity: int | None = Field(default=None, ge=1, le=999)
    ownership_grade: SportsGrade | None = None
    manual_value: float | None = Field(default=None, ge=0)
    manual_source_url: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)
    card: SportsCardDetails | None = None


class SportsPricesRequest(BaseModel):
    cardsight_ids: list[str] = Field(min_length=1, max_length=100)


class GradedValuesRequest(BaseModel):
    source: str = Field(min_length=2, max_length=120)
    source_url: str | None = None
    psa_7: float | None = Field(default=None, ge=0)
    psa_8: float | None = Field(default=None, ge=0)
    psa_9: float | None = Field(default=None, ge=0)
    psa_10: float | None = Field(default=None, ge=0)
    # Read the values from source_url's PriceCharting page instead (also
    # done when no values are given and source_url is a PriceCharting page).
    read_from_page: bool = False
