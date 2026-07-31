import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.router import api_router
from app.config.logging import configure_logging
from app.config.settings import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info(
        "application_started",
        extra={"environment": settings.environment, "version": __version__},
    )
    yield
    logger.info("application_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    application = FastAPI(
        title="Kraken Tax Companion API",
        description="Auditable API for Kraken tax-oriented workflows.",
        version=__version__,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    application.include_router(api_router)

    @application.exception_handler(RequestValidationError)
    async def validation_error(
        _: Request, error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "request_validation_failed",
                    "message": "Die Anfrage ist ungültig.",
                    "errors": [
                        {
                            "location": [str(part) for part in item["loc"]],
                            "message": item["msg"],
                            "type": item["type"],
                        }
                        for item in error.errors()
                    ],
                }
            },
        )

    @application.exception_handler(Exception)
    async def unexpected_error(_: Request, error: Exception) -> JSONResponse:
        logger.exception("unhandled_request_error", exc_info=error)
        return JSONResponse(
            status_code=500,
            content={
                "detail": {
                    "code": "internal_server_error",
                    "message": "Das Backend hat die Anfrage nicht verarbeiten können.",
                }
            },
        )

    return application


app = create_app()
