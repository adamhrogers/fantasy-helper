# Fantasy Helper — Proposed Repository Structure

**Status:** Draft v0.2, revised after Adam's review · companion to [architecture.md](architecture.md)

This is a monorepo with one Python package (`src/fantasy_helper/`) whose internal boundaries are linted, plus one PWA (`web/`). Every top-level package exists from M0 with only a docstring, so the import contracts cover every layer. Files and folders marked *(Mx)* inside them are created when that milestone starts, not before.

```
fantasy-helper/
├── README.md                         # what it is, how to run, the architecture diagram
├── CLAUDE.md                         # spec's working rules for AI-assisted sessions
├── pyproject.toml                    # uv project; ruff, mypy, pytest, import-linter config
├── uv.lock
├── .python-version                   # 3.12
├── .env.example                      # SYSTEM secrets by name only: FH_ANTHROPIC_API_KEY, LANGSMITH_*,
│                                     #   DATABASE_URL, FH_ENCRYPTION_KEY, VAPID_*, NTFY_TOPIC
│                                     #   (never league IDs or cookies: those live in the database)
├── .gitignore                        # .env, data/, node_modules/
├── .pre-commit-config.yaml           # ruff, import contracts, gitleaks
├── alembic.ini                       # migrations config (URL comes from DATABASE_URL)
│
├── .github/workflows/
│   ├── ci.yml                        # lint · types · tests (Postgres service container) · import contracts · secret scan
│   ├── web.yml                (M3)   # PWA typecheck · lint · build
│   ├── daily.yml              (M3)   # cron ≈04:30 ET: ingest → grade → evaluate → daily report
│   ├── weekly.yml             (M3)   # cron Mon: weekly overview + report card
│   └── evals.yml              (M6)   # regression replay when brain/ or prompts/ change
│
├── src/fantasy_helper/
│   ├── config/                       # pydantic-settings (system secrets only); role→model map; feature flags
│   ├── domain/                       # Pydantic models only. ZERO I/O. Imports nothing internal.
│   │   ├── users.py                  #   User, PlatformCredential (ciphertext only), EspnCredentials (in memory)
│   │   ├── league.py                 #   League, LeagueSettings, ScoringRules, RosterSlots, AcquisitionLimits
│   │   ├── player.py                 #   Player, PlayerRef, ExternalId
│   │   ├── stats.py                  #   StatLine, GameLog
│   │   ├── projections.py            #   ProjectionSnapshot, ProjectionRow, InjuryStatus
│   │   ├── takes.py                  #   WatchlistEntry, Take (direction, horizon, conviction)
│   │   ├── trades.py                 #   Trade, TradeSide
│   │   ├── recommendations.py        #   Recommendation, Claim, CallType, Grade, MissCause
│   │   ├── evaluations.py            #   EvaluationPoint (one point on a timeline graph)
│   │   ├── reports.py                #   ReportDocument, Card (the contract the PWA renders)
│   │   ├── runs.py                   #   RunRecord
│   │   └── ports.py                  #   Protocols: LeaguePlatform, ProjectionSource, NewsSource, StatsSource, Notifier, Clock
│   │
│   ├── adapters/
│   │   ├── _http.py                  # shared client: retries, backoff, rate limits, raw capture hook
│   │   ├── _recorder.py              # fixture capture + REDACTION (cookies, headers, leaguemate names)
│   │   ├── espn/                     # client.py (espn-api wrapper) · mapper.py · settings.py
│   │   ├── sleeper/                  # client.py · mapper.py · projections.py
│   │   ├── news/              (M4)   # injury / role-change feeds
│   │   └── yahoo/             (off)  # OAuth adapter, offseason
│   │
│   ├── identity/                     # canonical player registry + cross-platform ID crosswalk
│   │
│   ├── storage/
│   │   ├── db.py                     # engine/session factory (Supabase Postgres)
│   │   ├── crypto.py                 # AES-GCM encrypt/decrypt for platform credentials
│   │   ├── tables.py                 # SQLAlchemy 2.0 typed tables
│   │   ├── repositories/             # one per aggregate; every user-scoped query takes user_id
│   │   ├── raw_store.py              # immutable gzipped payloads (disk → R2 at M3)
│   │   └── migrations/               # Alembic
│   │
│   ├── engine/                       # PURE, deterministic. No storage/adapters/brain imports.
│   │   ├── scoring.py                # per-stat projection × league weights
│   │   ├── valuation.py              # replacement level; league-relative z-scores
│   │   ├── schedule.py               # games/week; startable games under lineup rules
│   │   ├── distributions.py          # per-game mean/variance in league scoring
│   │   ├── streaming.py              # add/drop/day plan within acquisition limits
│   │   ├── win_probability.py
│   │   ├── trajectory.py      (M6)   # realized-so-far + projected-remaining for a take/call/trade on a date
│   │   ├── lock_in.py         (M5)   # Sleeper optimal stopping
│   │   ├── signals.py         (M5)   # belief signals
│   │   └── baselines.py              # baseline call per call type
│   │
│   ├── facts/                        # FactSheet builder: the ONLY thing models read by default
│   │
│   ├── brain/                 (M4)   # No storage/adapters imports (enforced).
│   │   ├── llm.py                    # gateway: role→model, caching, cost capture, executor interface
│   │   ├── schemas.py                # ReportDraft, CallDraft, Debate (structured outputs)
│   │   ├── guardrails.py             # schema · numeric grounding · candidate-set checks
│   │   ├── graphs/                   # baseline.py (M4) · council.py (M8)
│   │   ├── agents/            (M8)   # analyst · scout · voice · skeptic · editor
│   │   └── prompts/                  # versioned prompt files + manifest (hash → prompt_version)
│   │
│   ├── mcp_server/            (M4)   # FastMCP; read-only tools over services
│   ├── registry/              (M4)   # append-only call log · tip-off guard · follow detection
│   │
│   ├── evaluation/            (M6)
│   │   ├── grader.py                 # closes windows, computes outcomes
│   │   ├── trajectories.py           # nightly: append evaluation_points; rebuild/backfill from snapshots
│   │   ├── metrics.py                # lift, Brier, calibration, MAE, bootstrap CI
│   │   ├── report_card.py            # grade scale, min-sample rule, best/worst calls
│   │   ├── miss_causes.py            # rules → LLM-judge suggestion → human confirm
│   │   ├── slicing.py                # platform × format × call type × confidence × user × setup
│   │   └── replay.py                 # regression + shadow runs over stored FactSheets
│   │
│   ├── reports/                      # ReportDocument builders: weekly (M2) · daily (M4) · scorecard (M6)
│   ├── delivery/              (M3)   # publish · web push (VAPID)
│   │
│   ├── pipeline/
│   │   ├── ingest.py                 # (M1) pull → raw → normalize; daily snapshots
│   │   ├── weekly.py                 # (M2)
│   │   ├── daily.py           (M4)
│   │   ├── grade.py           (M6)   # grade + evaluate (trajectory points)
│   │   ├── preflight.py              # data checks that stop the run (fail closed)
│   │   └── runlog.py                 # one record per run
│   │
│   ├── observability/                # logs (redacting) · tracing (LangSmith) · alerts (ntfy) · cost accounting
│   ├── api/                   (M3)   # FastAPI backend-for-frontend
│   │   ├── app.py
│   │   ├── auth.py                   # Supabase JWT verification; user_id on every request
│   │   └── routes/                   # reports · leagues · watchlist · takes · trajectories · feedback · push · scorecard
│   └── cli/                          # Typer: `fh version | keygen | doctor` (M0); `league add | ingest | run | grade | fixtures record | mcp serve | replay` later
│
├── web/                       (M3)   # PWA: Vite + React + TypeScript
│   ├── package.json
│   ├── vite.config.ts                # vite-plugin-pwa (manifest, service worker)
│   ├── public/                       # icons, manifest.webmanifest
│   └── src/
│       ├── app/                      # shell, routing, tab bar, sign-in
│       ├── features/                 # today/ · this-week/ · watchlist/ · scorecard/ · onboarding/ (M7) · chat/ (later)
│       ├── components/cards/         # swipeable decision cards + feedback buttons
│       ├── components/charts/ (M6)   # timeline graph: horizon band, event markers, final-grade marker (Recharts)
│       ├── api/                      # client generated from FastAPI OpenAPI
│       └── sw/                       # push handler, notification click → deep link
│
├── tests/
│   ├── conftest.py
│   ├── fixtures/recorded/            # REDACTED real responses: espn/<league>/…, sleeper/<league>/…
│   ├── unit/                         # engine, facts, guardrails, identity, crypto
│   ├── contract/                     # each adapter vs recorded responses
│   ├── golden/                       # hand-calculated scoring cases (M2 exit criterion, forever)
│   └── integration/                  # full pipeline on fixtures + Postgres
│
├── evals/
│   ├── setups/                       # baseline.yaml · council-v1.yaml (graph + role→model + prompt versions)
│   └── datasets/              (M6)   # LangSmith dataset exports of graded days
│
├── scripts/                          # one-off ops: record fixtures, backfill trajectories, seed a test league
├── infra/                     (M3)
│   ├── Dockerfile                    # one image: API + jobs
│   └── cloudrun/                     # service + job definitions
│
└── docs/
    ├── architecture.md
    ├── repo-structure.md
    ├── roadmap.md
    ├── decisions/                    # ADRs: 0001-python-langgraph-langsmith.md, 0002-one-mcp-server.md, …
    ├── spikes/                (M1)   # per-stat projections, game logs, news, lock visibility, injury timing
    ├── runbooks/                     # ESPN cookie refresh · failed run · late report · onboarding a friend
    └── case-study/                   # weekly metric snapshots · before/after reports · trace screenshots
```

## Conventions

- **Naming:** the package is `fantasy_helper` and the CLI is `fh`. Modules are nouns and functions are verbs.
- **Configuration:** system secrets live in environment variables. User, league and credential data live in the database. Nothing sensitive ever lives in the repo (architecture §12.1).
- **Local runtime data:** `data/` (gitignored) holds the raw payload store until it moves to R2 at M3.
- **Fixtures:** only recorded through `fh fixtures record`, which always redacts before writing. Hand-edited fixtures aren't allowed.
- **Prompts:** plain files in `brain/prompts/`. The manifest hashes each one into a `prompt_version` that is stored on every call.
- **ADRs:** one per architecture choice, numbered and never deleted. A reversed decision gets a new ADR that supersedes the old one.
- **Import contracts** (in `pyproject.toml`): `domain` is a leaf. `engine` doesn't import `storage`, `adapters` or `brain`. `brain` doesn't import `storage` or `adapters`. Interfaces (`api`, `cli`, `mcp_server`) are imported by nothing.
