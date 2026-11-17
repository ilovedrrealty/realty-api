from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from database import get_db
from schemas.market_reports import MarketReport
from services.market_reports import canonical_market_location, public_report


router = APIRouter(tags=["market reports"])
NO_STORE = {"Cache-Control": "no-store"}


@router.get("/site/market-reports", response_model=MarketReport)
def market_report(
    request: Request,
    response: Response,
    location: str | None = Query(None, description="One recognized listing area"),
    db: Session = Depends(get_db),
):
    if location is not None:
        canonical = canonical_market_location(location) if len(location) <= 80 else None
        if canonical is None or len(request.query_params.getlist("location")) != 1:
            raise HTTPException(422, "Choose one recognized listing area.", headers=NO_STORE)
        location = canonical
    response.headers.update(NO_STORE)
    return public_report(db, location)
