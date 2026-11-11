"""Talkbalt platform API: accounts, each business's setup, bookings, call history and analytics,
plus the internal API the voice agent (backend-web) uses.

The browser reaches it through the Next.js server (frontend-web/app/api/backend), so session
cookies stay first-party; CORS only matters for tools calling the API directly in development.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# backend-web/.env.local, resolved from this file so the cwd doesn't matter.
load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")

from api.analytics import router as analytics_router  # noqa: E402  (needs the env loaded)
from api.auth import router as auth_router  # noqa: E402
from api.history import router as history_router  # noqa: E402
from api.internal import router as internal_router  # noqa: E402
from api.org import router as org_router  # noqa: E402
from api.public import router as public_router  # noqa: E402

CORS_ORIGINS = [
    o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3001").split(",") if o.strip()
]

app = FastAPI(title="Talkbalt API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)
for router in (auth_router, org_router, public_router, history_router, analytics_router, internal_router):
    app.include_router(router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
