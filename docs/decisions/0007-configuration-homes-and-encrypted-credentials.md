# ADR 0007: Where configuration lives, encrypted credentials and row-level security

**Status:** Accepted · 2026-10-19

## Decision
- **System secrets** (API keys, the database URL, the encryption key) live in environment variables: a git-ignored `.env` locally, GitHub Secrets for jobs, Secret Manager for the API.
- **User and league data** (users, leagues, league IDs, watchlists, takes) live in the database, scoped by `user_id`, behind sign-in.
- **Platform credentials** (ESPN cookies, later Yahoo tokens) live in the database, encrypted with AES-256-GCM. The associated data binds each ciphertext to its user and platform, so a ciphertext copied to another row won't decrypt.
- **Every table has row-level security enabled with no policies.** Our backend connects as the owner and bypasses RLS, while Supabase's public Data API can read nothing. A test fails if any table lacks RLS.
- Settings match environment variables **by alias only**. The app's Claude key is `FH_ANTHROPIC_API_KEY`, and the shell's `ANTHROPIC_API_KEY` is ignored (a test covers this).

## Why
Adam's review: user data and logins belong in a database with authentication, not in config. The database password can't live inside the database, and the encryption key can't sit next to what it protects, so system secrets stay in the environment.

## Rejected
- **League IDs and cookies in `.env`:** doesn't scale past one user, and puts friends' logins in a file.
- **Plaintext cookies in the database:** a database leak would expose them.

## Consequences
For the paid launch, move the key to a managed key service (Cloud KMS or Supabase Vault) with rotation. `key_version` on each credential row allows that.
