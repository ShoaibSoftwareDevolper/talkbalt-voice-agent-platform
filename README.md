# Talkbalt platform API

The users' side of Talkbalt: accounts, each business's info, services and products, opening hours, bookings, call history, analytics, and the public link and widget API. FastAPI, with its **own PostgreSQL and Redis**. The voice agent (`backend-web`) is a separate service with its own database and Redis; it reaches this API only through `/internal/*`.

```
browser ──> frontend-web (Next.js proxy) ──/api/*──> platform-api ──> platform Postgres (5435) + platform Redis (6382)
                                                        ▲
                     voice agent (backend-web) ──/internal/* (INTERNAL_API_TOKEN)
                                                        └── voice Postgres (5436) + voice Redis (6383)
```

## Setup and run

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Docker.

```bash
cd platform-api
uv sync
cp .env.example .env.local                                        # then fill in the values
bash scripts/setup_docker_db.sh                                   # one time: Postgres + DATABASE_URL
bash scripts/setup_docker_redis.sh talkbalt-platform-redis 6382   # one time: Redis + REDIS_URL
scripts/dev.sh                                                    # containers, migrations, API on :8001
```

Both setup scripts are idempotent, bind to `127.0.0.1` only, and never print passwords. `INTERNAL_API_TOKEN` must be the same value in `backend-web/.env.local`.

## Redis

Redis speeds things up but is never the source of truth: if it's down, rate limits let requests through and the cache is skipped.

- **Rate limits** (shared by every API process): public calls (5 per visitor per 10 minutes, 60 per business per hour) and logins (10 per email and 10 per address per 15 minutes).
- **Business runtime cache:** the setup the voice agent loads at the start of each call (profile, services, products, hours, FAQs), for 5 minutes. Any change from the dashboard deletes it, so the next call gets the new setup.

## Endpoints

Run it from `platform-api`:

```bash
uv run uvicorn api.main:app --port 8001 --reload
```

The browser reaches it through the Next.js server (`frontend-web/app/api/backend`), so the session cookie stays first-party.

| Endpoint | Description |
| --- | --- |
| `POST /api/auth/signup` | `{name, email, password, business_name}`: creates the user, the business (organization), an owner membership, its profile and default hours (Mon-Sat 10:00-20:00), and signs in. |
| `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` | Session in an httpOnly `tb_session` cookie; only its SHA-256 is stored. Passwords use argon2id. |
| `GET/PUT /api/org/profile` | Business info: name, greeting (spoken word for word), description, time zone, location (in person, remote or both, with address), contact email and website, common questions with answers, online calls on/off, and the read-only public `slug`. |
| `GET/POST /api/org/services`, `PUT/DELETE /api/org/services/{id}` | Services (bookable, with a length) and products (asked about, not booked), with prices; visible or hidden. |
| `GET/PUT /api/org/hours` | One window per weekday; a day without a window is closed. |
| `GET /api/org/bookings?from=&to=`, `POST /api/org/bookings/{id}/cancel` | Bookings in the business's time zone (default: the next 30 days). |
| `POST /api/org/test-call` | A LiveKit token whose agent dispatch metadata names the business, for the dashboard's test call. |
| `GET /api/public/{slug}` | A business's public info for its link page and widget: about, location, services, products, hours, FAQs. No sign-in. |
| `POST /api/public/{slug}/call` | A call token for a visitor of the link or widget. Rate limited per visitor (5 per 10 minutes) and per business (60 per hour), in memory; refused when the business turned online calls off. |
| `GET /api/calls`, `/api/calls/{id}`, `/api/calls/{id}/recording`, `/api/calls/{id}/metrics`, `GET /api/analytics` | The signed-in business's calls only. |
| `GET /api/usage` | Platform-wide usage against the providers' free tiers, for Talkbalt's own admins (`PLATFORM_ADMIN_EMAILS`). |

**Tenant isolation:** every business table (`agent_profiles`, `services`, `business_hours`, `customers`, `bookings`) has `org_id` and Postgres row level security. `session_scope(org_id=...)` switches the transaction to the `talkbalt_app` role and sets `app.org_id`, so even a query without a filter only sees that business's rows. **No double booking:** the `bookings_no_overlap` exclusion constraint refuses overlapping active bookings of one business, even when two calls book the same slot at once.

**Multi-tenant agent:** a call whose dispatch metadata has `org_id` loads that business's profile, services and hours while waiting for the caller (one short transaction). The agent introduces the business, quotes its services, and only offers times inside its hours. When the caller has confirmed every detail (including the read-back phone number), code saves the booking (`agent/bookings.py`); if the slot was just taken, the agent offers the nearest free times. Calls without `org_id` (console mode, the old demo flow) behave as before, using `BUSINESS_*` env settings and booking nothing.

## Internal API (voice agent only)

All routes need the `X-Internal-Token` header and are hidden from the OpenAPI docs; the web app's proxy only forwards `/api/*`.

| Endpoint | Description |
| --- | --- |
| `GET /internal/businesses/{org_id}/runtime` | The business's setup for a call (cached in Redis). |
| `POST /internal/calls` | Registers a call when it starts. The agent chooses the call id, so bookings made during the call can reference it. Idempotent. |
| `POST /internal/calls/{call_id}/report` | The finished call: transcript, per-turn metrics, events, usage totals. The platform computes the cost estimate. Idempotent, so the agent's outbox can retry it. |
| `POST /internal/bookings` | Books a slot: customer upsert plus booking. Overlaps are refused by the database, and the answer then lists the nearest free times that day. |

## Metrics, events and cost estimates

The voice agent buffers metrics in memory during a call and sends them once, in the call report; they are stored in one transaction:

- `call_turn_metrics`: one row per user or agent turn. User turns have STT audio seconds, end-of-turn and transcription delays. Agent turns have LLM requests, first token, duration, prompt/cached/completion/reasoning tokens, tokens per second, TTS first audio, characters and audio seconds, end-to-end latency, `interrupted` and `cancelled`. Sources: per-plugin `metrics_collected` (LLM, STT, TTS) and `ChatMessage.metrics`. Reasoning tokens come from Groq's `completion_tokens_details` and are already included in completion tokens. A request cut short (truncation net, interruption) can end before Groq reports usage, so its tokens are null.
- `call_events`: truncation_net, end_call_blocked/allowed, llm_error, llm_fallback, rate_limited, interruption, recording_status_change, booking_created, booking_conflict. Details never contain caller content.
- Per-call totals and `estimated_cost_usd` on `calls`, from `config/pricing.toml`. **Fill in the TODO prices there** (Groq, Deepgram, LiveKit pricing pages); unset components are listed as missing instead of guessed.

Analytics are per business, in its own time zone (daily aggregates, latency avg and p95 per stage). `GET /api/usage` (this month vs the providers' free tiers in `config/pricing.toml`, counted by Talkbalt, not fetched from providers) is for platform admins only.

