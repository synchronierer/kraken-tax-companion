from fastapi import APIRouter

from app.api.workflows import router as workflows_router
from app.health.router import router as health_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(workflows_router)
