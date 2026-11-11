"""Typed view of config.yaml. Thresholds, model names, and prices live there."""

from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GraphSettings(_Strict):
    request_budget_s: float


class GuardSettings(_Strict):
    max_chars: int
    short_text_chars: int
    english_min_prob: float
    other_language_min_prob: float


class ClassifierSettings(_Strict):
    base_model: str
    fallback_model: str
    prompted_small: str
    prompted_large: str
    max_new_tokens: int
    temperature: float


class ExtractorSettings(_Strict):
    model: str
    temperature: float


class WriterSettings(_Strict):
    model: str
    temperature: float
    max_words: int


class TripSettings(_Strict):
    max_required_stops: int
    max_modes_to_compare: int


class LonLat(_Strict):
    lon: float
    lat: float


class HighwayWeights(_Strict):
    motorway: float
    trunk: float
    trunk_on_revise: float


class BikeLaneWeights(_Strict):
    missing: float
    missing_on_revise: float


class ElevationWeights(_Strict):
    slope_above_6: float
    slope_above_4_on_revise: float


class QuietScenicWeights(_Strict):
    primary_or_secondary: float
    primary_or_secondary_on_revise: float


class CustomModelWeights(_Strict):
    avoid_highways: HighwayWeights
    avoid_tolls: float
    avoid_ferries: float
    prefer_bike_lanes: BikeLaneWeights
    minimize_elevation: ElevationWeights
    prefer_quiet_or_scenic: QuietScenicWeights


class MapSettings(_Strict):
    http_timeout_s: float
    attempts: int
    wait_s: float
    geocode_country: str
    downtown_toronto: LonLat
    origin_match_radius_km: float
    detour_min_km: float
    detour_fraction: float
    custom_model: CustomModelWeights


class VerifierSettings(_Strict):
    bike_network_min_share: float
    highway_max_m: float
    flat_max_climb_m_per_km: float
    distance_tolerance: float
    duration_tolerance: float
    loop_tolerance: float
    arrive_by_buffer_min: float
    sponsor_max_distance_m: float
    sponsor_min_rating: float


class TokenPrice(_Strict):
    input: float
    cached_input: float
    output: float


class TierPrice(_Strict):
    free_monthly: int
    then: float


class GraphHopperPrice(_Strict):
    credits_per_route_2_to_10_points: float
    credits_per_round_trip: float
    free_credits_per_day: int
    free_plan_commercial: bool
    basic_eur_per_month: float
    basic_credits_per_day: int


class Prices(_Strict):
    openai_usd_per_million_tokens: dict[str, TokenPrice]
    mapbox_usd_per_thousand: dict[str, TierPrice]
    graphhopper: GraphHopperPrice


class Settings(_Strict):
    graph: GraphSettings
    guard: GuardSettings
    classifier: ClassifierSettings
    extractor: ExtractorSettings
    writer: WriterSettings
    trip: TripSettings
    maps: MapSettings
    verifier: VerifierSettings
    prices: Prices


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load .env and config.yaml once per process."""
    load_dotenv()
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return Settings.model_validate(raw)
