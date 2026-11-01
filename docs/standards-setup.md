# The Standards Setup Caches (`actions/standards-setup`, `standards-seed.yml`)

- **Status:** Implemented. The Standards Check uses the action now. A repository's caches fill
  once its push, schedule and manual `standards.yml` calls `standards-seed.yml` (starter change
  in `Rubio-Enterprises/standards`).
- **Issue:** standards#835. It follows standards#821: only the default branch saves a cache.

## Purpose

Both required checks of an onboarded repository start with `mise run setup`: the injected
Standards Check and the starter Test Gate. On a plain runner, setup downloads every tool the
Standards Release pins that the runner lacks, and `npm ci` fetches the repository's packages.
Measured on `linux-x64-small` on 2026-11-01, setup took 85 s of a 331 s check: 34 s for 12 tools,
then two minutes for 121 packages. Every job paid it.

Both checks run only on pull requests. A cache saved by a pull-request run is visible only to
later runs of that pull request, so a cache saved there never served a first run. (The check's
`jdx/mise-action` cache never saved at all: mise-action saves only after its own install, and
the check leaves the install to `setup`.) Caches saved on the default branch are visible to every
pull request, so the default branch seeds them and pull requests restore them.

## The caches

| Cache | Path | Key | Pull-request fallback |
| --- | --- | --- | --- |
| mise tool installs | `$MISE_DATA_DIR/installs` and `shims` | `standards-mise-v1-<runner os>-<arch>-<os release>-<mise>-<interpreter>-<week>-<mise files>` | same week, then any week |
| npm package cache | `${npm_config_cache:-~/.npm}` | `standards-npm-v1-<runner os>-<arch>-<os release>-<week>-<lockfiles>` | same week, then any week |
| trivy database | `~/.cache/trivy/db` | `trivy-db-<os>-<week>` | none |

- **Keys are named before setup.** Setup creates `node_modules` (with nested lockfiles) and the
  conf.d link into `.standards/`, so a hash taken afterwards would differ from the restore's. For
  the same reason the mise key hashes only the repository's own mise files. The weekly part bounds
  how long a cache misses tools a newer Standards Release added; on a fallback hit, `setup` installs
  just the difference.
- **The interpreter is part of the mise key.** mise's pipx tools are uv venvs bound to the
  interpreter `uv python find` resolves, so a cache never crosses interpreters.
- **Only `installs` and `shims`.** On the glue runner image, the rest of the mise data directory is
  the image's own mise binary; restoring an old copy over it would downgrade it.
- **The seed restores only its exact key.** On a miss it runs setup cold and saves a fresh cache,
  so a cache holds exactly what that week's tool set and lockfile need. Fallback restores, which
  would carry every older version forward, happen only in pull requests, which never save.
- **The check never saves**, not even the trivy database: on a busy pool, a save there cost 45 s
  for a cache that only the same pull request could restore. A cold check downloads trivy's
  database in about 7 s.

## Who calls what

| Caller | Event | Mode |
| --- | --- | --- |
| `standards.yml` (the injected Standards Check) | pull request, merge group | restore; `restore: false` on a CI Image runner |
| starter `test-gate.yml` | pull request, merge group | restore |
| `standards-seed.yml`, called by the starter `standards.yml` | push to the default branch, weekly schedule, manual | `save: "true"` |

`standards-seed.yml` routes through `project-route.yml` and uses the same uv, mise and mise
version as the check, so it runs where the repository's pull requests run, and the keys and the
cache versions (path list and compression method) match. It does nothing on another branch, or
when the repository's route is its CI Image, which already carries the tools and dependencies.

## Things that are easy to get wrong

- **Cache versions include the path strings.** The seed and the check must compute the same
  paths. Both get them from the action's key step, which reads `MISE_DATA_DIR` and
  `npm_config_cache` as the runner sets them.
- **Callers need `packages: read`** for setup's install of the internal package. The token also
  raises mise's GitHub API rate limit.
- **Starter files belong to each repository.** A repository's `standards.yml` and `test-gate.yml`
  pick up the seed job and the action only through a rollout or a merge of `starter-drift`'s
  report. The action itself follows `main`, so later cache changes need no rollout.
