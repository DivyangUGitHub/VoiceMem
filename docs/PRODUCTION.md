# Running VoiceMem in production

This layer wraps the research runtime under `web/` and `voicemem/`. It adds user accounts,
per-user memory, sign-in, request limits, health checks, logs, metrics, backups, a locked
dependency set and a hardened container. It does not change how memory works.

**Read [Before you launch](#before-you-launch) first.** The isolation between users is
tested with a stand-in for the models. It has not been run against the real models.

## How it fits together

```
Browser / script
  |  HTTPS, WSS
  v
production.asgi:app
  |-- SecurityMiddleware      sign-in, origin check, limits, headers; labels each request with its user
  |-- Observability           request id, JSON access log, metrics
  |
  |-- /                       Next.js app: sign-up, sign-in, consent, account. Signed-in users go on to /backend/
  |-- /auth/*                 sign-up, sign-in, sign-out, who am I (public, rate limited)
  |-- /account/*              view, consent, change password, export, delete (needs sign-in)
  |-- /healthz, /readyz       for the platform
  |-- /metrics, /api-docs     need sign-in
  `-- /backend/**             the voice console (the product screen) and the runtime behind it, needs sign-in

Postgres (DATABASE_URL)       accounts only
Data volume                   every user's memory spaces, under VOICEMEM_MEMORYSPACE_ROOT
```

## Run it

```bash
cp .env.production.example .env.production
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste as VOICEMEM_SECRET_KEY
# also set OPENAI_API_KEY and POSTGRES_PASSWORD (letters and digits only) in .env.production
docker compose --env-file .env.production -f docker-compose.production.yml up --build
```

Open `http://localhost:8787`. Sign-up is off by default. Set `VOICEMEM_ALLOW_SIGNUP=1` to
let people create accounts, or leave it off and use an access key (below) for a first user.

The app refuses to start with no secret key and no API keys, with a secret key under 32
characters, or when it cannot reach the database. Set `VOICEMEM_AUTH_DISABLED=1` only on
your own machine: it turns sign-in off and runs everything as one shared user.

### Render

`render.yaml` builds the Dockerfile, creates a Postgres database, generates
`VOICEMEM_SECRET_KEY`, and attaches a 10 GB disk at `/var/lib/voicemem`. Create a Blueprint
from the repository. Render asks for `OPENAI_API_KEY`. Keep the web service at one
instance. It sets `FORWARDED_ALLOW_IPS=*` because Render's proxy is the only way in; on any
other host set it to your proxy's address, or every user shares one rate-limit bucket.

### Models

The image does not contain model weights. They live on the data volume under
`/var/lib/voicemem/models` (`VOICEMEM_MODELS_DIR`). Fetch them with
`scripts/download_models.sh` before the first real session. Without them the runtime tries to
download the small embedding model from huggingface.co at start-up and exits if it cannot
reach it (for example behind a company proxy that re-signs HTTPS, which shows up as
`CERTIFICATE_VERIFY_FAILED`). Downloaded files are cached on the data volume, so this
happens once. Other models are fetched the first time a feature needs them and can be
several GB.

## People and their data

**Accounts.** Email and password, stored in Postgres. Passwords are hashed with scrypt.
Sessions are signed cookies (12 hours, `HttpOnly`, `SameSite=Strict`) that carry a version
number, so changing the password signs out every session, including a stolen one.

**Access keys.** `VOICEMEM_API_KEYS` still works for scripts and for a first sign-in. Each
key is its own separate user with its own memory. A key holder has no account record, so
the `/account` endpoints refuse them.

**Separate memory.** Each request is labelled with its user. The runtime then uses that
user's own memory spaces, and a user cannot name or reach another user's space. Memory
language, the active space, the last recording and the recently retrieved memories are all
per user. Background work started by the library keeps the label.

**Consent.** A signed-in user must agree before the voice connection opens. The consent
screen says the app processes voice, learns a voiceprint and keeps recordings. Withdrawing
consent closes the door again.

**Their rights.** From the Account button in the console's sidebar (it opens `/?account`), or `/account/*`:

| Action | Endpoint |
|---|---|
| Download everything as a zip (account facts plus a consistent copy of every space) | `GET /account/export` |
| Change password (signs out everywhere) | `POST /account/password` |
| Withdraw or give voice consent | `POST /account/consent` |
| Delete the account, every memory space and all recordings (asks for the password) | `POST /account/delete` |

Recordings are saved inside the user's own space, so deleting the space deletes them. The
existing archive code is documented as removing recordings after 30 days. That is from its
comments; it has not been tested here.

Set `NEXT_PUBLIC_PRIVACY_URL` when you build the frontend to show a link to your privacy
policy on the consent screen. The text of that policy is yours to write.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `OPENAI_API_KEY` | none | Credentials for the LLM and TTS calls. `/readyz` fails without it. |
| `VOICEMEM_SECRET_KEY` | none | Signs sessions and turns on accounts. 32+ characters. |
| `DATABASE_URL` | SQLite file in `VOICEMEM_DATA_DIR` | Use Postgres in production. `postgres://` URLs are accepted. |
| `VOICEMEM_ALLOW_SIGNUP` | `0` | `1` lets anyone create an account. |
| `VOICEMEM_API_KEYS` | none | Optional keys for scripts, 16+ characters each. |
| `DEMO_MODE` | `llm_tts` | `realtime` needs OpenAI Realtime access. |
| `VOICEMEM_MEMORYSPACE_ROOT` | `/var/lib/voicemem/voicemem_memoryspace` | Where memory is stored. Keep it on the volume. |
| `VOICEMEM_ALLOWED_ORIGINS` | none | Extra browser origins allowed to call the API. Same-origin always works. |
| `VOICEMEM_RATE_LIMIT_PER_MIN` | 120 | Protected requests per client per minute. |
| `VOICEMEM_LOGIN_LIMIT_PER_MIN` | 10 | Sign-in and sign-up attempts per client per minute. |
| `VOICEMEM_MAX_WS_PER_CLIENT` | 4 | Concurrent WebSockets per client. |
| `VOICEMEM_MAX_BODY_BYTES` | 1000000 | Largest accepted request body. |
| `VOICEMEM_MAX_WS_MESSAGE_BYTES` | 1000000 | Largest WebSocket message. Larger closes with 1009. |
| `VOICEMEM_ENABLE_DOCS` | `0` | `1` serves `/api-docs` (still needs sign-in). |
| `VOICEMEM_LOG_FORMAT` | `json` | `json` or `text`. |
| `VOICEMEM_LOG_LEVEL` | `INFO` | Standard level names. |
| `FORWARDED_ALLOW_IPS` | uvicorn default | Proxy addresses allowed to set `X-Forwarded-For`. |

Never put secrets in `NEXT_PUBLIC_*` variables. Next.js embeds those in the browser bundle.

## What the security layer does

- `/backend`, `/account`, `/metrics` and the docs need a signed-in session or a key header.
- State-changing requests and WebSocket connections from another origin are refused, even with valid credentials.
- Rate limits cover sign-in, sign-up, rejected credentials, protected requests and WebSocket connects.
- Oversized bodies (including chunked ones) and oversized WebSocket messages are refused.
- Every response carries a Content-Security-Policy and the usual hardening headers. HSTS is sent only over HTTPS. API and auth responses are not cached.
- Dot-segment and double-slash paths are refused. Space names are reduced to safe characters and forced under the caller's own prefix.

Covered by `tests/test_security.py`, `tests/test_account_flow.py` and
`tests/test_tenant_isolation.py`. Rate-limit counters live in memory, which is correct for
one instance and wrong for several.

## Health and monitoring

| Path | Use |
|---|---|
| `/healthz` | Process is up. Liveness probe. |
| `/readyz` | Frontend built, memory folder writable, `OPENAI_API_KEY` set, database reachable. 503 with a per-check reason if not. It never calls a paid API. |
| `/metrics` | Prometheus text format. Needs sign-in. |

Logs are one JSON object per line on stdout, each with a request id that is also returned in
the `X-Request-ID` header. Query strings are never logged. Probe requests are logged only
when they fail.

## Backups

Memory lives on the data volume and accounts live in Postgres. Back up both.

```bash
python scripts/backup.py /backups --keep 14        # run daily from cron
python scripts/backup.py --verify /backups/voicemem-20261009T010000Z
```

Each run writes a folder with a consistent copy of every memory space (using SQLite's own
backup API, so a file being written is not copied half-way) and the accounts database
(`pg_dump`, or the SQLite backup API). It then checks the result and exits non-zero if the
backup is bad. Old backups beyond `--keep` are removed. Copy the folder off the machine,
for example with `rclone` or `aws s3 sync`; a backup on the same disk does not survive the
disk.

To restore, stop the app, then:

```bash
cp -r <backup>/memory/. "$VOICEMEM_MEMORYSPACE_ROOT"/     # memory
psql "$DATABASE_URL" < <backup>/accounts.sql             # Postgres, into an empty database
# SQLite instead: copy <backup>/accounts.db to the path in DATABASE_URL
```

Practise a restore into a spare environment before you rely on it. Your hosting plan may
also offer disk and database snapshots; check what it includes.

## Dependencies and CI

`requirements.lock` pins the Python dependencies for Python 3.11, the image's version.
Regenerate it with:

```bash
uv pip compile pyproject.toml --python-version 3.11 -o requirements.lock --no-header
```

The frontend is locked by `frontend/pnpm-lock.yaml` and built with `--frozen-lockfile`. To
shrink the image by several GB, build with CPU-only torch:

```bash
docker build --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu -t voicemem .
```

CI (`.github/workflows/production-check.yml`) runs on every push and pull request to
`main`: `ruff`, the test suite against a real Postgres, the frontend build, a Docker build
and a dependency audit. The audit fails on any known-vulnerable pin except the dated
entries in `security/audit-exceptions.txt`. The one entry is `transformers==4.52.3`, pinned
on purpose for the Qwen2.5-Omni emotion model. Its exception expires on 2027-01-31, after
which CI fails until someone reviews it. Upgrading it needs a GPU regression run.

## Before you launch

These are the things this repository cannot check for you.

1. **Two users on the real runtime.** The isolation tests use a stand-in for the models.
   On a machine with the real models, sign in as two users at the same time, give each a
   different fact, ask each for it, and confirm neither ever hears the other's. Do it with
   one user on a Chinese space and one on English too. Do not skip this: it is the check
   that matters most.
2. **A real deployment.** Deploy to a staging copy of your host, create an account, give
   consent, hold a voice conversation, export your data, then delete the account and check
   the memory folder is empty.
3. **A restore.** Take a backup and restore it somewhere else.
4. **Legal text.** Write a privacy policy, link it with `NEXT_PUBLIC_PRIVACY_URL`, and check
   which privacy law applies to your users. Voiceprints are biometric data.
5. **Licenses.** The code is Apache-2.0. Check the licenses of the Qwen models and the
   ChatMem-400K dataset before selling anything built on them.

## Not built yet

- **Password reset and email verification.** The app sends no email. A user who forgets a password cannot recover the account; an operator would have to delete it. Adding it needs an email provider.
- **Sign-in throttling beyond per-address limits.** Many addresses guessing one account are not slowed down.
- **Admin tools.** No page to list, suspend or remove users, and no usage limits or billing.
- **Several instances.** Models, memory instances, rate-limit counters and each user's active space live in process memory. Run one instance. Scaling out needs a shared store such as Redis and sticky routing for WebSockets.
- **Schema migrations.** Tables are created at start. Add Alembic before the first schema change.
- **Account deletion edge case.** Deleting an account frees the user's memory instances without closing their database files explicitly. On Linux this is safe; check it on your platform.
- **Capacity.** The local models need GPU planning that this repository does not do for you.

## Do not delete

`web/`, `voicemem/`, `models/`, `evaluation/`, `finetune/`, `examples/`, `assets/`,
`docs/` and `tests/` are the research runtime and its supporting material. The production
layer only adds files around them.
