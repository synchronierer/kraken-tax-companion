from fastapi import APIRouter

from app.api.financial_reviews import router as financial_reviews_router
from app.api.kraken_live import router as kraken_live_router
from app.api.kraken_sync import router as kraken_sync_router
from app.api.sale_proposals import router as sale_proposals_router
from app.api.tax import router as tax_router
from app.api.workflows import router as workflows_router
from app.health.router import router as health_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(workflows_router)
api_router.include_router(financial_reviews_router)
api_router.include_router(tax_router)
api_router.include_router(sale_proposals_router)
api_router.include_router(kraken_live_router)
api_router.include_router(kraken_sync_router)
