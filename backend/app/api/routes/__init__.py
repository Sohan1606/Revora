from fastapi import APIRouter

from app.api.routes import (
    admin, analytics, auth, experiments, health, integrations, policies_admin,
    public, recovery, simulator, webhooks,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(recovery.router)
api_router.include_router(analytics.router)
api_router.include_router(experiments.router)
api_router.include_router(policies_admin.router)
api_router.include_router(simulator.router)
api_router.include_router(webhooks.router)
api_router.include_router(integrations.router)
api_router.include_router(public.router)
