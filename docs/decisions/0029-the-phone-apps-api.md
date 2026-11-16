# ADR 0029: The phone app's API: a service that checks every token itself

**Status:** Accepted · 2026-11-16 · builds on ADR 0008 (Supabase), 0020 (hot reads) and 0025 (Cloud Run)

## Decision
- **The API checks each sign-in token itself, against Supabase's public keys.** It fetches the project's JWKS once and keeps it (ten minutes), then verifies the signature, the issuer (`<project URL>/auth/v1`), the audience (`authenticated`) and the expiry on every request. The accepted algorithms are listed (`ES256`, `RS256`); a token that names anything else, `HS256` and `none` included, is refused before any key is looked at. Anonymous sign-ins are refused. Nothing from `user_metadata` is trusted, because the user can edit it: only `sub`, `email` and the role Supabase sets.
- **A sign-in is tied to our own user by its id, not its email.** Migration 0007 adds `users.auth_user_id` (nullable, unique). The first time a token arrives whose email matches a user with no `auth_user_id`, the API sets it, in one statement (`UPDATE ... WHERE auth_user_id IS NULL AND email = ... RETURNING`); after that only the id is looked at. No match: 403. **The API never creates a user.** Adam exists before he can sign in, and friends will be invited the same way at the beta.
- **The service is the same image, started with `fh serve`** (uvicorn on `$PORT`). One Dockerfile and one build; `container.yml` starts the image with `serve` and asks `/healthz`.
- **It has its own account and its own three secrets:** `fh-api` reads `fh-api-database-url` (Supabase's **transaction pooler**, port 6543, a different string from the jobs'), `fh-supabase-url` and `fh-app-origins`. No encryption key, no R2, no ping key: a bug or a leak in the API can't decrypt a league login or touch the raw store. A test fails if a change gives it any of them.
- **Public at Cloud Run, private in the app.** The phone can't present Google credentials, so the platform lets anyone reach the service (the project owner grants `allUsers` the invoker role once, in `scripts/gcp-setup.sh`; the deployer can't change IAM and the deploy never asks for it) and every route except `/healthz` requires a valid token. At most two instances, scaling to zero, so an unused API costs nothing.
- **CORS is an allow-list** read from `FH_APP_ORIGINS`. Until the app has an address the secret holds `https://not-set.invalid`, because a secret with no value can't be mounted. Tokens travel in the `Authorization` header, never in a cookie, so cross-site request forgery has nothing to ride on.
- **The schema is committed.** `fh openapi` prints it, `web/openapi.json` holds it, and a test fails when they differ, the same idea as `alembic check`. The phone app's typed client is generated from that file.
- **The service moves last in the deploy,** after the migration, so it never runs ahead of its schema (ADR 0027).
- **Adam signs in with an email and a password; the emailed 6-digit code is for the beta.** Both give the same kind of token, so the API doesn't care. (Updated 2026-11-16: the code needs a custom email sender and an edited template in Supabase, which is a lot of setup for one user. Friends can't be handed a password, so the beta uses the code, and the sender is part of the beta's setup.)
- **No docs, no OpenAPI route, no access log.** Nothing about the API is browsable from outside, and uvicorn's access log would carry every URL.

## Why
The app needs somewhere to read reports and save a push subscription, and friends will need accounts at the beta. Checking tokens locally means no round trip to Supabase on every request (ADR 0020) and no outage of theirs becomes ours. Holding only public keys means nothing in this service could forge a sign-in. Tying the sign-in to an id means a changed email can't hand someone else's reports over, and the same code is what the beta's invitations need. A separate account and secrets keep the one internet-facing piece away from the key that decrypts league logins: it is the likeliest place for a mistake, so it gets the least.

## Rejected
- **The shared signing secret (`HS256`):** the service would hold something that can forge any user's token.
- **Asking Supabase's `/user` on every call:** slower, and Supabase's outage becomes ours.
- **Letting the app read Postgres through Supabase's Data API:** ADR 0008. The API stays the one place access is enforced, and the tables have row-level security with no policies on purpose.
- **Matching users by email on every request:** emails change; ids don't.
- **Creating a user at first sign-in:** it would turn sign-up into something anyone could do. Sign-ups are off in Supabase, and the smoke script proves it.
- **Reusing the report jobs' account and database URL:** the session pooler is the wrong pooler for a service with short requests, and the jobs' account can read the encryption key.
- **A "dev user" switch that skips the token check:** it is the kind of flag that ends up on in production. Tests replace the sign-in dependency instead.
- **Private at Cloud Run (Google sign-in required):** the phone can't present Google credentials.

## Consequences
- **Phase 2's rollout order matters** (`docs/m3-plan.md`, `docs/runbooks/phone-app.md`): `scripts/gcp-setup.sh infra` creates the account, the secrets and a placeholder service from the pull request's branch, Adam fills the three secrets, then the merge deploys the real image. A deploy before the service exists fails, which is the deployer's limit working as intended.
- **If an organization policy blocks `allUsers`,** the setup step fails and the fallback is a different front door (a Cloudflare Worker that forwards with its own credentials); this project has no such policy.
- **If the first deploy fails with `PERMISSION_DENIED`,** grant the deployer the narrowest role that fixes it, on the service, and record it here; never `run.admin` on the project.
- **Supabase's `anon` and `service_role` keys are retiring (end of 2026).** The app uses the publishable key. The API uses neither.
- `scripts/api-smoke.sh` is the check: 200s with Adam's token and his real report counts, 401 without one, and "sign-ups refused" for an unknown email. It prints statuses and counts only.
