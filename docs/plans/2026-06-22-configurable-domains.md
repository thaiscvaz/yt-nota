# Plan — Make vault domains configurable (decouple taxonomy from source)

**Status:** proposed · awaiting approval
**Tier:** Small (isolated, < 10 atomic steps, reversible pre-publish)
**Goal:** Remove the personal 7-domain taxonomy from public source code so `yt-nota`
can ship as a generic, forkable tool — without breaking the owner's live pipeline.
**Reference pattern:** mature self-hosted notes tools — reusable tool, personal
taxonomy/config lives outside the repo via a configurable + gitignored layer with
a committed `.example`.

## Problem

`VALID_DOMINIOS` (the 7 vault domains) is hardcoded in `src/yt_nota/config.py:24`.
It is a 3-way contract:

- `config.py` defines the frozenset (public source → leaks the owner's taxonomy)
- `domain.py` validates against it; `vault.py` iterates it to locate/write notes
- `tests/test_vault.py` asserts real domain names (`IA-Engenharia`, `Carreira`)

A cosmetic edit of the example YAML does not fix this (the names still ship in
`config.py`), and editing `config.py` directly **breaks the owner's flow**: her personal
`channel_domains.yaml` maps channels to `IA-Engenharia` etc., which `validate()` would
then reject and `vault.py` would fail to locate.

## Design

Move the valid-domain set from hardcoded source into the same configurable layer that
`channel_domains.yaml` already uses (personal, gitignored) + a committed generic
`.example`. Mirror the existing `_load_mapping()` cascade exactly.

- New `config/domains.yaml` — personal, **gitignored**, lists the owner's real domains.
- New `config/domains.example.yaml` — committed, generic placeholder domains.
- `config.py` exposes `get_valid_dominios() -> frozenset[str]` (lru_cache), reading
  `domains.yaml` → fallback `domains.example.yaml`. Plus `reload_dominios()` to clear cache.
- `domain.py` / `vault.py` call `config.get_valid_dominios()` (module-qualified, so tests
  can monkeypatch it) instead of importing the constant.
- Tests override the domain set via an autouse fixture → fully decoupled from both the
  owner's personal config and the committed example.

YAML format (`domains.example.yaml`):

```yaml
# Valid vault domains. Copy to domains.yaml (gitignored) and edit with your own.
- Technology
- Finance
- Health
- Career
- Hobby
- Productivity
- Research
```

`domains.yaml` (personal, gitignored) keeps the current real set:
`IA-Engenharia, Financas, Saude, Carreira, Impressao-3D, Metodo, Mestrado`.

## Files — the list is the law

| File | Change |
|---|---|
| `config/domains.yaml` | **NEW** — personal, gitignored; the owner's 7 real domains (migration step, preserves flow) |
| `config/domains.example.yaml` | **NEW** — committed; 7 generic placeholder domains |
| `.gitignore` | add `config/domains.yaml` (next to `channel_domains.yaml`) |
| `src/yt_nota/config.py` | remove `VALID_DOMINIOS` frozenset (24-32); add `DOMINIOS_CONFIG_PATH`, `get_valid_dominios()` (lru_cache, cascade), `reload_dominios()` |
| `src/yt_nota/domain.py` | drop `VALID_DOMINIOS` import; call `config.get_valid_dominios()` at lines 40, 43, 68 |
| `src/yt_nota/vault.py` | drop `VALID_DOMINIOS` import (25); call `config.get_valid_dominios()` at 522, 553 |
| `src/yt_nota/cli.py` | help text 432-433: drop the 7-domain enumeration → "see config/domains.yaml" |
| `README.md` | line 107: replace the 7-domain enumeration with a generic description |
| `CLAUDE.md` | line 25: soften "um dos 7 domínios" → "a configurable set (see config/domains.yaml)" |
| `skills/yt-sintese/SKILL.md` | line 20: drop the 7-domain enumeration → generic wording |
| `tests/conftest.py` | **NEW (or extend)** — autouse fixture monkeypatching `config.get_valid_dominios` to a fixed neutral test set + `reload_dominios()` reset |
| `tests/test_vault.py` | retarget asserts `IA-Engenharia`→neutral, `Carreira`→neutral (lines 39,43,380,398-411,481) to match the fixture set |

**Sanitization (added during execution):** real channel/author names removed from
public files — `channel_domains.example.yaml` and `tests/test_slug.py` now use generic
placeholders; personal run artifacts (queues, error dumps, ad-hoc scripts) gitignored.
Real curation stays in gitignored files only (the public repo is the tool).

**Out of scope / untouched:** build artifacts (`egg-info`, `PKG-INFO`
regenerate on build, not tracked).

## Execution order

1. **Migration first (preserves flow):** create `config/domains.yaml` with the 7 real
   domains + add to `.gitignore`. Harmless before the code change; ready after it.
2. `config.py`: add loader/path/reload, remove the hardcoded frozenset.
3. `domain.py` + `vault.py`: switch call sites to `config.get_valid_dominios()`.
4. Create `config/domains.example.yaml` (generic, committed).
5. Genericize docs: `cli.py` help, `README.md`, `SKILL.md`, `CLAUDE.md`.
6. Tests: add conftest fixture, retarget `test_vault.py` asserts.
7. Validate (gates below).

## Validation gates

- `pytest` — all 89 tests green (CI-equivalent: only `domains.example.yaml` present →
  fixture controls the set, so tests pass regardless of personal config).
- **Owner flow intact:** with `config/domains.yaml` present, run a real resolve
  (`yt-nota <known-url> --dry-run` or equivalent) → resolves to a real domain
  (e.g. `IA-Engenharia`), writes under `30-Recursos/Literatura/<dominio>/`.
- **Generic fallback:** temporarily move `domains.yaml` aside → `get_valid_dominios()`
  returns the generic example set; `validate()` rejects `IA-Engenharia` with a clear error.
- `grep -rn` for any of the 7 personal domain names across tracked files → only
  `config/domains.yaml` (gitignored) should match; zero hits in committed source/docs.

## Risk & rollback

- Work on branch `refactor/configurable-domains`; existing `backup-pre-sanitize-*` branch
  remains untouched. Repo is still **private** — everything reversible until the publish step.
- Primary risk: owner's flow breaks if `domains.yaml` is missing when code lands.
  Mitigated by execution step 1 (create it first).
- Publish (make repo public, push `main` only) is a separate, owner-driven step **after**
  all gates pass — not part of this plan.
