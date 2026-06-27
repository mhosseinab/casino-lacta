---
type: blueprint
project: casino-games
codename: lacta
tags: [casino, monorepo, polyglot, uv, pnpm, task, seam-architecture, solid, production-readiness, dev]
status: READY (sets the S1 scaffold target)
slug: casino-games
date: 2026-06-27
augments: 2026-06-27_casino-games_build-plan_v2.md  # §2 repo layout — this refines it into a polyglot monorepo
companions: [2026-06-27_casino-games_implementation-steps.md, 2026-06-27_casino-games_plan.md]
---

# Casino (lacta) — Monorepo & Seam Blueprint

The structural blueprint **S1 scaffolds against**. Refines build-plan_v2 §2 into an explicit
**polyglot monorepo** and pins the **seams** (SOLID/DRY/KISS made concrete) so every swappable
dependency is a config/adapter change, not a rewrite. Read alongside `CLAUDE.md` (Design principles)
and build-plan_v2 §3 (contracts).

## 1. Why a monorepo (it passes the bar, it isn't cargo-culted)

The monorepo skill says *don't* if there are <3 packages and no shared code. Here both tests pass:
- **Two languages** — Python (`engine`/`app`/`verifier`/`tests`) + TypeScript (`client`).
- **Genuinely shared code** — `engine/` is pure outcome logic imported VERBATIM by `app/` (to
  settle bets), `verifier/` (to prove them), and `tests/` (to gate RTP). One implementation, three
  consumers — the DRY core of the whole design. A split repo would either duplicate it (fatal: the
  verifier would no longer prove the server) or publish it to a registry (overkill).

So: one repo, multiple workspace-managed packages.

## 2. Tooling (the polyglot stack)

| Concern | Tool | Why |
|---|---|---|
| Python workspace | **uv** (single root `.venv`, members) | One lockfile; `engine` installs editable; deps isolate purity (below) |
| TS workspace | **pnpm** workspaces | `client` + generated contract types as `workspace:*` |
| Cross-language orchestration | **Task** (`Taskfile.yml`) | One entry point: `task test`, `task lint`, `task dev`, `task rtp` across both languages |
| Git hooks | **lefthook** (`glob_matcher: doublestar`) | Parallel per-language hooks; ruff/biome on commit, mypy/tsc/import-linter on push |
| Seam enforcement | **import-linter** | A *contract* fails CI if `engine` imports a framework or `app` (purity/layering by construction) |
| Client↔server contract | **openapi-typescript** | Generate TS types from FastAPI's OpenAPI schema — one contract, two languages |
| Per-language config | root `pyproject.toml` (ruff+mypy), `biome.json`, `.editorconfig` | Each scanner walks up to the root config |

**No buf/Protobuf.** The contract here is the HTTP/WS JSON API (Pydantic-defined). Generating TS
types from the OpenAPI schema is the right-sized seam; protobuf would be ceremony for a play-money
portfolio. (If a high-throughput Go Crash sidecar is ever built — build-plan §8 seam — revisit.)

## 3. Layout

```
casino-lacta/                         # repo root — uv + pnpm workspaces, Task, lefthook
├── pyproject.toml                    # uv workspace root: members = [engine, app, verifier]; ruff + mypy (strict)
├── uv.lock                           # committed
├── pnpm-workspace.yaml               # packages: client, packages/contracts-ts
├── package.json                      # root: lefthook, openapi-typescript, biome
├── biome.json  .editorconfig  .dockerignore  .gitignore
├── Taskfile.yml                      # cross-language orchestration (includes py/ts taskfiles)
├── lefthook.yml                      # cross-language git hooks
├── .importlinter                     # seam contracts (engine purity + layering)
├── docker-compose.yml                # postgres:16, redis:7, api
├── alembic.ini
├── .github/workflows/ci.yml          # paths-filter (py/ts) + the rtp-gate job
├── engine/                           # PURE package — uv member; deps = stdlib ONLY
│   ├── pyproject.toml                #   (no fastapi/sqlalchemy/redis → purity is a dependency fact)
│   └── src/engine/                   #   rng.py fairness.py money.py types.py registry.py
│       ├── games/ (+_curve.py)  slots/  table/  poker/  cards/evaluator.py
├── app/                              # FastAPI service — uv member; depends: engine{workspace}, fastapi, sqlalchemy…
│   ├── pyproject.toml
│   ├── Dockerfile                    # build context = repo root; uv sync --package app --no-dev --no-editable
│   └── src/app/                      #   main.py db/ wallet/ auth/ api/ games/bet_loop.py ws/{crash,poker}.py economy/ rg/
├── verifier/                         # uv member; depends: engine{workspace} ONLY  (+ a static page)
│   ├── pyproject.toml
│   └── src/verifier/  +  web/
├── tests/                            # RTP/EV, determinism, parity, rules, sidepots, concurrency, load/
├── migrations/                       # Alembic (owned by app)
├── client/                           # Vite + React + TS thin renderer (pnpm member) — presentation only
│   ├── package.json                  # depends: @lacta/contracts (workspace:*)
│   └── src/  +  vitest config (RTL, no Playwright at this stage)
└── packages/contracts-ts/            # GENERATED TS types from app's OpenAPI schema (committed; the client's only contract)
```

> **DECIDED (2026-06-27): the member split is the layout** — `engine`/`app`/`verifier` are uv
> workspace members with `src/` layout, so engine purity is a *dependency fact*, not just a
> convention. The S1–S42 steps, plan, and build-plan have been adapted to it: module **import names
> are unchanged** (`import engine.rng`, `import app…`), the purity grep `git grep … -- engine/`
> still scopes correctly, and `python -c "import engine.rng"` still works — only literal *file
> paths* carry the `src/` segment (`engine/games/dice.py` → `engine/src/engine/games/dice.py`). S1
> scaffolds the workspace; every later step inherits it.

## 4. The three (+1) monorepo problems, solved

1. **Dependency linking** — `engine`/`verifier`/`app` link from disk via uv `{ workspace = true }`;
   `client` links the generated contract via pnpm `workspace:*` (symlink, never a stale `file:`).
2. **Task ordering + caching** — Task `sources:`/`generates:` skip unchanged work; `contracts`
   generation runs before `client` build/test; `rtp` is its own heavy task. CI uses paths-filter so
   a client-only change never runs the Python suite and vice-versa.
3. **Single tooling config** — one `pyproject.toml` (ruff + mypy strict) and one `biome.json` at the
   root; every member inherits. `.editorconfig` sets cross-language whitespace.
4. **Cross-language contract** — `task contracts` runs the API, dumps `openapi.json`, and regenerates
   `packages/contracts-ts`. A drift check in CI fails if the committed types are stale → the client
   can never silently diverge from the server's shapes.

## 5. Seam enforcement (purity + layering, by construction)

Three layers of defense, cheapest first:
- **Dependency isolation** — `engine/pyproject.toml` lists NO framework deps, so `engine` *cannot*
  import `fastapi`/`sqlalchemy`/`redis` even by accident.
- **import-linter contracts** (`.importlinter`, run in CI + pre-push):
  ```ini
  [importlinter]
  root_packages = engine, app, verifier

  [importlinter:contract:engine-is-pure]
  name = engine imports only stdlib + engine
  type = forbidden
  source_modules = engine
  forbidden_modules = app, verifier, fastapi, sqlalchemy, redis, httpx, random, secrets, time, datetime, os, asyncio, pathlib

  [importlinter:contract:layering]
  name = app/verifier may depend on engine, never the reverse
  type = layers
  layers = app | verifier
           engine
  ```
- **The grep** (`git grep -nE "import (random|secrets|fastapi|sqlalchemy|redis|httpx|asyncio|os|time|datetime|pathlib)|[^.]\bopen\(" -- engine/`) —
  the fast reviewer gate. It mirrors the `forbidden_modules` list above (import-linter now covers all
  of them too) plus a bare `open(` file-IO check, so a reviewer can spot a purity breach locally
  without running the linter.

## 6. Adding a package (checklist)

- **Python member:** create `<name>/pyproject.toml` + `src/<name>/`; add to root `[tool.uv.workspace] members`; declare intra-repo deps as `{ workspace = true }`; add its dir to the CI `py` paths-filter and the import-linter layers.
- **TS member:** create `package.json`; add the glob to `pnpm-workspace.yaml`; consume internal packages via `workspace:*`; add to the CI `ts` paths-filter.
- Wire `task` targets (`dir:` per member) and lefthook globs.

## 7. Production-readiness checklist (what "best practice" still needs)

The build kit ships the *correctness* spine (ledger, fairness, RTP-gate, tests, observability S39,
load test S40, deploy S41). The items below are what separate a correct demo from a
production-grade service. **[have]** = in the kit/this blueprint; **[gap]** = add before calling it
production-ready. Prioritized.

**P0 — architecture/scale seam (do before horizontal deploy)**
- **[gap] Single-authoritative-actor scaling.** Crash rounds and poker tables assume "one
  process/partition." For >1 instance you need explicit **partition routing / leader election**
  (consistent-hash a round/table id → owning instance, or a lock in Redis) or the actor invariant
  breaks (double settlement). Today it's a documented seam; production needs it wired or a pinned
  single writer per partition. This is the #1 production risk.
- **[have] Idempotent ledger + recovery-from-committed-seed** already make a single-writer restart safe.

**P1 — security**
- **[gap] Rate limiting + abuse controls** — per-IP/per-user limits (slowapi/Redis) on bet, auth,
  faucet; WS connection caps + backpressure; request-size/timeout limits.
- **[gap] Auth hardening** — short-lived access + rotating refresh, token revocation list, CORS
  allow-list, security headers (HSTS/CSP/X-Frame), brute-force lockout on the guest→account path.
- **[gap] Secrets management** — a real store (SOPS/Doppler/cloud SM) + rotation, `.env.example`
  committed / `.env` gitignored, no secrets in CI logs. **[have]** platform-env-only rule.
- **[gap] Supply chain** — Renovate/Dependabot, `bandit`+`semgrep` (SAST), `gitleaks` (secret scan),
  `trivy` (container scan), pinned base images, SBOM. Branch protection + required checks.

**P2 — reliability & data**
- **[gap] Graceful shutdown** — drain WS, finish in-flight settlements, release partition locks; add
  **readiness vs liveness** probes (only `/health` exists today).
- **[gap] DB hardening** — pool sizing, statement timeouts, **backups + PITR**, restore drills,
  online/zero-downtime migration discipline (additive-first), a scheduled reconciliation job (not
  just the function). **[have]** double-entry + reversible migrations + `reconcile()`.
- **[gap] Resilience** — retry/circuit-breaker on Redis/DB; the reconciler for stuck rounds is S20
  (Crash) — generalize to poker.

**P3 — observability & ops**
- **[have]** structured logs, Prometheus metrics, per-game RTP drift monitor (S39).
- **[gap]** distributed tracing (OpenTelemetry), error tracking (Sentry, incl. client source maps),
  dashboards + **alerts on SLOs** (ledger-reconciliation drift, RTP drift, p95 latency, WS
  disconnect rate, stuck-bet count), audit-log retention policy, on-call **runbooks** (ledger
  discrepancy, stuck round, seed-rotation).

**P4 — delivery & frontend**
- **[gap]** CI/CD: lefthook pre-commit/push (this blueprint), coverage gate, conventional-commits +
  changelog, staging→prod promotion, the **OpenAPI→TS contract drift check**.
- **[gap]** frontend prod: CSP, error boundaries, a11y audit, bundle budget, env config, Sentry.
- **[have]** Vitest/RTL component tests; **no Playwright at this stage** (deliberate).

**P5 — compliance (play-money, but still)**
- **[have]** persistent "not real money / no prizes" badge, RG limits/self-exclusion (S38),
  anti-collusion (S35), minimal PII.
- **[gap]** GDPR account data-deletion endpoint, privacy policy + ToS, age-gate, data-retention
  policy, ADRs for the decided forks (plan §4).

## 8. Bootstrap (after S1 scaffolds)

```bash
task doctor        # verify uv, pnpm, node, docker present
task setup         # uv sync --group dev ; pnpm install --frozen-lockfile ; lefthook install
task contracts     # generate packages/contracts-ts from the API OpenAPI schema
task dev           # docker compose up postgres redis + uvicorn (reload) + vite
task lint test     # ruff+mypy+import-linter+biome ; pytest + vitest
task rtp           # the heavy RTP-as-CI gate
```

## Related
[[2026-06-27_casino-games_build-plan_v2]] (§2 layout, §3 contracts) · [[2026-06-27_casino-games_plan]]
(§4 forks) · [[2026-06-27_casino-games_implementation-steps]] (S1 scaffolds this) · `CLAUDE.md`
(Design principles · Map · Commands)
