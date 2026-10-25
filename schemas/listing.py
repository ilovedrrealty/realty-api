from pydantic import BaseModel, Field
from typing import Annotated, Optional, List, Literal
from decimal import Decimal
from datetime import datetime
import uuid

Bathrooms = Annotated[float, Field(ge=0, multiple_of=0.5)]


class ListingCreate(BaseModel):
    title: str
    description: Optional[str] = None
    description_es: Optional[str] = None
    type: str
    transaction: str
    price: Decimal
    location: str
    bedrooms: Optional[int] = None
    bathrooms: Optional[Bathrooms] = None
    area_sqft: Optional[int] = None
    lot_size_sqft: Optional[int] = None
    roi: Optional[Decimal] = None
    seller_financing: bool = False
    hoa: bool = False
    hoa_fee: Optional[Decimal] = None
    tax_exempt: bool = False
    gated_community: bool = False
    construction_status: Optional[str] = None
    year_built: Optional[int] = None
    features: Optional[List[str]] = None
    maps_url: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    tag: Optional[str] = None
    tags: Optional[List[str]] = None
    video_links: Optional[List[str]] = None
    tour_3d_url: Optional[str] = None
    utilities: Optional[str] = None
    included_utilities: Optional[List[str]] = None
    association_fee: Optional[Decimal] = None
    deposit_policy: Optional[str] = None
    images: Optional[List[str]] = None
    co_listing_enabled: bool = False
    co_listing_brokerage: Optional[str] = None
    co_listing_agent_name: Optional[str] = None
    co_listing_agent_contact: Optional[str] = None
    co_listing_agent_email: Optional[str] = None
    co_listing_brokerage_email: Optional[str] = None
    co_listing_brokerage_phone: Optional[str] = None
    co_listing_commission_split: Optional[Decimal] = None
    co_listing_notes: Optional[str] = None
    co_listing_status: Optional[str] = None
    currency: Optional[str] = None
    source_ref: Optional[str] = None
    price_per_day: Optional[Decimal] = None
    price_per_month: Optional[Decimal] = None
    owner_paypal_email: Optional[str] = None
    co_listing_agreement_accepted: bool = False
    co_listing_agreement_url: Optional[str] = None


class ListingUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    description_es: Optional[str] = None
    type: Optional[str] = None
    transaction: Optional[str] = None
    price: Optional[Decimal] = None
    location: Optional[str] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[Bathrooms] = None
    area_sqft: Optional[int] = None
    lot_size_sqft: Optional[int] = None
    roi: Optional[Decimal] = None
    seller_financing: Optional[bool] = None
    hoa: Optional[bool] = None
    hoa_fee: Optional[Decimal] = None
    tax_exempt: Optional[bool] = None
    gated_community: Optional[bool] = None
    construction_status: Optional[str] = None
    year_built: Optional[int] = None
    features: Optional[List[str]] = None
    maps_url: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    tag: Optional[str] = None
    tags: Optional[List[str]] = None
    video_links: Optional[List[str]] = None
    tour_3d_url: Optional[str] = None
    utilities: Optional[str] = None
    included_utilities: Optional[List[str]] = None
    association_fee: Optional[Decimal] = None
    deposit_policy: Optional[str] = None
    images: Optional[List[str]] = None
    co_listing_enabled: Optional[bool] = None
    co_listing_brokerage: Optional[str] = None
    co_listing_agent_name: Optional[str] = None
    co_listing_agent_contact: Optional[str] = None
    co_listing_agent_email: Optional[str] = None
    co_listing_brokerage_email: Optional[str] = None
    co_listing_brokerage_phone: Optional[str] = None
    co_listing_commission_split: Optional[Decimal] = None
    co_listing_notes: Optional[str] = None
    co_listing_status: Optional[str] = None
    currency: Optional[str] = None
    price_per_day: Optional[Decimal] = None
    price_per_month: Optional[Decimal] = None
    owner_paypal_email: Optional[str] = None
    co_listing_agreement_accepted: Optional[bool] = None
    co_listing_agreement_url: Optional[str] = None


class ListingResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: Optional[str]
    description_es: Optional[str] = None
    type: str
    transaction: str
    price: Decimal
    location: str
    bedrooms: Optional[int]
    bathrooms: Optional[float]
    area_sqft: Optional[int]
    lot_size_sqft: Optional[int]
    roi: Optional[Decimal]
    seller_financing: bool
    hoa: bool
    hoa_fee: Optional[Decimal]
    tax_exempt: bool
    gated_community: bool
    construction_status: Optional[str]
    year_built: Optional[int]
    features: List[str]
    maps_url: Optional[str]
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    tag: Optional[str]
    tags: List[str] = []
    video_links: List[str] = []
    tour_3d_url: Optional[str] = None
    utilities: Optional[str] = None
    included_utilities: List[str] = []
    association_fee: Optional[Decimal] = None
    deposit_policy: Optional[str] = None
    images: List[str]
    share_image_url: Optional[str] = None
    status: str
    rejection_reason: Optional[str] = None
    is_deal: bool = False
    deal_discount_value: Optional[Decimal] = None
    deal_discount_type: str = 'pct'
    view_count: int = 0
    co_listing_enabled: bool = False
    co_listing_brokerage: Optional[str] = None
    co_listing_agent_name: Optional[str] = None
    co_listing_agent_contact: Optional[str] = None
    co_listing_agent_email: Optional[str] = None
    co_listing_brokerage_email: Optional[str] = None
    co_listing_brokerage_phone: Optional[str] = None
    co_listing_commission_split: Optional[Decimal] = None
    co_listing_notes: Optional[str] = None
    co_listing_status: Optional[str] = None
    currency: str = "USD"
    search_price: Optional[Decimal] = None
    search_price_currency: Optional[str] = None
    search_price_period: Optional[str] = None
    source_ref: Optional[str] = None
    price_per_day: Optional[Decimal] = None
    price_per_month: Optional[Decimal] = None
    owner_paypal_email: Optional[str] = None
    co_listing_agreement_accepted: bool = False
    co_listing_agreement_url: Optional[str] = None
    leads_count: int = 0
    has_pending_deal_request: bool = False
    has_pending_edit: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    submitted_by: uuid.UUID
    submitted_by_name: Optional[str] = None
    submitted_by_email: Optional[str] = None
    owner_id: Optional[uuid.UUID]
    assigned_realtor_id: Optional[uuid.UUID] = None

    model_config = {"from_attributes": True}


class AdminListingResponse(ListingResponse):
    submitted_by_name: Optional[str] = None
    submitted_by_email: Optional[str] = None
    reviewed_by_name: Optional[str] = None
    reviewed_by_email: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    assigned_realtor_name: Optional[str] = None
    assigned_realtor_email: Optional[str] = None

    model_config = {"from_attributes": True}


class ListingPageResponse(BaseModel):
    items: List[ListingResponse]
    total: int
    page: int
    page_size: int
    median_price: Optional[float] = None
    avg_roi: Optional[float] = None

    model_config = {"from_attributes": True}


class PublicListingResponse(ListingResponse):
    location_precision: Literal["exact", "area", "hidden"] = "exact"
    location_verified: bool = False


class PublicListingPageResponse(ListingPageResponse):
    items: List[PublicListingResponse]


class ListingFilterOption(BaseModel):
    value: str
    count: int = Field(ge=0)


class ListingFilterOptionsResponse(BaseModel):
    locations: List[ListingFilterOption]
    types: List[ListingFilterOption]


class ListingMapPoint(BaseModel):
    id: uuid.UUID
    title: str
    location: str
    latitude: float
    longitude: float
    price: Optional[Decimal] = None
    currency: str
    transaction: str
    price_period: Optional[Literal["month", "day"]] = None
    location_verified: bool = False


class ListingMapArea(BaseModel):
    location: str
    count: int = Field(ge=0)


class ListingMapBounds(BaseModel):
    south: float
    west: float
    north: float
    east: float


class ListingMapResponse(BaseModel):
    items: List[ListingMapPoint]
    total: int = Field(ge=0)
    mapped_total: int = Field(ge=0)
    unmapped_total: int = Field(ge=0)
    areas: List[ListingMapArea]
    ungrouped_total: int = Field(ge=0)
    viewport_total: int = Field(ge=0)
    returned: int = Field(ge=0)
    limit: int
    truncated: bool
    bounds: Optional[ListingMapBounds] = None


class AdminListingPageResponse(BaseModel):
    items: List[AdminListingResponse]
    total: int
    page: int
    page_size: int

    model_config = {"from_attributes": True}


class AdminRejectBody(BaseModel):
    reason: str


class AdminAssignListingBody(BaseModel):
    realtor_id: Optional[str] = None


class BulkImportRowResult(BaseModel):
    row: int
    source_ref: Optional[str] = None
    status: str  # succeeded | skipped | failed
    listing_id: Optional[uuid.UUID] = None
    message: Optional[str] = None


class BulkImportJobResponse(BaseModel):
    id: uuid.UUID
    status: str
    total_rows: int
    processed_rows: int
    succeeded_count: int
    skipped_count: int
    failed_count: int
    results: List[BulkImportRowResult] = []
    created_at: datetime
    completed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
