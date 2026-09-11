# Hack&lt;IT&gt; — working agreement

Five hours, solo, 10:00–15:00. Score is the only objective. Nothing else
counts: not coverage, not architecture, not a tidy diff.

---

## How to work with me today

**Name the target.** "Add a `swap_pairs` planner in `src/planners/local.py`"
beats "improve the planner". I should never be guessing which file you mean.

**One instruction per message.** A four-part message gets a four-part answer
where part three is wrong and you do not notice until 14:40.

**No tests.** Do not write them, do not ask me to. The exception is a bug you
have seen twice and cannot localise — then one focused check, deleted after.
`tools/selftest.py` already covers the two things that fail silently (the exact
solver and the record store); it does not need extending.

**Background anything over ~20 seconds.** Long solve runs go in the background
and you keep working. Never sit watching a progress bar.

**Short budgets while iterating.** `--time 3` while you are changing planner
logic. Long budgets are for the run you leave going while you do something
else, and for the last half hour.

**Say what you saw, not what you expected.** Paste the actual table row, the
actual traceback. I will reason from the output; I cannot reason from "it
didn't work".

**Do not refactor.** If it is ugly and it scores, it ships.

---

## The order of work

Do these in order. Skipping ahead is how people spend four hours optimising a
planner whose score they cannot verify.

**1. Get a valid submission on every level. (target: 10:40)**
Empty plans count. `python tools/gen_probes.py` writes an empty-action-list
submission for every level — upload those first. A zero on the board beats a
blank on the board, and it proves the submission format is right before you
have anything worth submitting.

**2. Recover the scoring formula. Before any planner work. (target: 11:30)**
Read the spec for the scoring rules, write the probes in `tools/gen_probes.py`,
upload them, drop the logs into `log/`, run `python tools/fit.py`. You are done
when it prints UNIQUE and the coefficients are round. Until then every local
score is a guess and you cannot tell an improvement from noise.

**3. Verify the engine against the portal. (target: 12:00)**
Submit one non-trivial plan, download its log, run
`python tools/diff_trace.py traces/<the trace> log/<the log>`. It must report no
divergence. If your simulator and the portal disagree, your planner is climbing
the wrong hill and the leaderboard will tell you so at 14:00.

**4. Now optimise.** Everything before this was setup. This is the only phase
where planner cleverness pays. Two functions in
`src/planners/anneal.py` — `_initial` and `_mutate` — are the whole job; the
search loop around them already works. Write `_mutate` first and spend your
remaining time on it, not on the temperature schedule.

**Before each new thing, check what is at zero or unverified.** `tools/run.py`
prints this at the bottom of every run. A level with no score, a level scoring
zero, or an unverified formula is worth more than any incremental gain on a
level that already works. Fix the broken thing first — always.

---

## Two rules that cost the afternoon if broken

### Solve the coefficients. Never fit them.

`tools/fit.py` solves the system exactly over the rationals. It has no
least-squares path and must not grow one. There are exactly three honest
answers: UNIQUE, UNDERDETERMINED (it will tell you which probe to run next), or
INCONSISTENT (the score is not linear in those components, or a log is
mis-parsed).

A formula that is approximately right is worse than no formula. No formula
makes you cautious; an approximate one makes you confident and wrong, and it
ranks two close plans in the wrong order.

**If a recovered constant is not suspiciously round, the derivation is not
finished.** Real scoring formulas use 1000, 250, 1/2 — not 104729 and not
0.8473. A non-round constant means a missing component, a unit error, or a
threshold you have not spotted. `fit.py` flags these and refuses `--write`.
Do not override it by hand-editing `formula.json` to make a number match.

### Determinism is a submission requirement

Fixed seeds, no wall-clock dependence, no hash-order dependence. Concretely:

- `ctx.rng` (seeded) — never the bare `random` module, never `random.seed()`
  at import time. Multi-seed search makes this sharper, not looser: the whole
  fleet collapses to one result if planners ignore `ctx.seed`, and you will
  read a spread of zero as "deterministic planner" when it is really "broken
  planner".
- Never `time.time()` inside a planner. `ctx.time_left()` for pacing only,
  never as an input to a decision.
- Never Python's `hash()` — it is salted per process, so the same code gives
  different answers on different days. `zlib.crc32` if you need a stable hash.
- Iterating a `set`, or a `dict` built from set iteration, is the classic silent
  one. Wrap in `sorted()`.

**Flag this when you see it.** If I am writing or reviewing code that sorts or
iterates over a set or a dict in an order that could vary between runs, say so
at the time — do not wait to be asked. Same for any of the above.

---

## The map

Strict separation, so that when the portal disagrees with you there is exactly
one place to look:

> **engine computes facts · scoring applies weights · planners decide**

A number that is a count belongs in the engine. A number that is a weight
belongs in `formula.json`. A number that is a preference belongs in a planner.

| Path | Owns |
|---|---|
| `src/engine/model.py` | `Plan`, `Trace`, `StepTrace`, `Level`, `Components`. Serialisation, plan hashing. `Plan.to_submission_text()` is the only place that knows the upload format. |
| `src/engine/level.py` | Reading `levels/`. `parse_level()` is the TODO. |
| `src/engine/simulate.py` | The simulator. Replays an action list, emits cumulative components per step. **No weights.** Write `_real`, flip `ACTIVE`. |
| `src/scoring.py` | components → score. Exact `Fraction` arithmetic. Raises `UnknownComponent` when the engine measures something the formula does not price — that is a bug, not a warning. |
| `src/planners/__init__.py` | Planner registry, `Context` (level, seed, deadline). `@register("name")` and run.py finds it. |
| `src/planners/search.py` | Local-search loop: acceptance, cooling, restarts, warm start from `best/`, budget handling, incremental-problem interface. Generic -- no problem knowledge. |
| `src/planners/anneal.py` | `climb` + `anneal`. **`_initial` and `_mutate` are the two functions you write.** |
| `src/planners/*.py` | One file per approach. The only place decisions live. |
| `src/workspace.py` | Repo paths. `tools/_paths.py` re-exports these. |
| `tools/run.py` | **The main loop.** Parallel multi-seed solve → score → compare → keep winners. |
| `tools/fit.py` | Exact recovery of the formula from logs. |
| `tools/exact_linalg.py` | Rational Gauss-Jordan behind fit.py. Reports unique / underdetermined / inconsistent, names conflicting logs, ranks the next probe. |
| `tools/gen_probes.py` | Calibration submissions, one component each. Stub — fill in after the briefing. |
| `tools/diff_trace.py` | First point where my trace and a portal log diverge. |
| `tools/check_incremental.py` | Whether an incremental score still agrees with full replay. Run after writing one, and after every new move type. |
| `tools/portal_log.py` | Log parsing patterns. **Edit this first** once you have seen a real log; fit.py and diff_trace.py both depend on it. |
| `tools/submit.py` | Emit every submission from `best/`, verify it is not stale, build a reproducible zip. No solving. |
| `tools/record.py` | `best-known.json` read-modify-write, atomic, locked. |
| `tools/selftest.py` | Solver + record store checks. Run if you touch either. |
| `best-known.json` | Best score per level, with budget, seed, timestamp, components. |
| `best-known.history.jsonl` | Append-only, every run. A flat tail means the level has plateaued — move on. |
| `submissions/` | What to upload. `best/` — the winning plan per level. |
| `traces/` | Engine output, for diffing. Regenerated, not precious. |
| `docs/`, `levels/`, `log/` | Spec, level files, downloaded logs. |

Keep this table current as the shape changes, so neither of us has to ask where
something lives.

---

## Commands

```bash
python tools/run.py                      # everything, 30s budget each
python tools/run.py --time 3             # fast iteration
python tools/run.py --level 2 --time 60  # one level, long budget
python tools/run.py --seeds 16           # wider seed fleet on a stochastic planner
python tools/run.py --seeds 1            # one seed, for debugging
python tools/run.py --dry-run            # score without recording
python tools/run.py --serial             # one process, for debugging a planner
python tools/fit.py                      # recover the formula
python tools/fit.py --write              # save it, if UNIQUE and round
python tools/gen_probes.py               # calibration submissions
python tools/diff_trace.py TRACE LOG     # first divergence
python tools/check_incremental.py --planner anneal   # audit the fast path
python tools/run.py --compare climb,anneal --seeds 8 # is the change real?
python tools/submit.py                   # emit every submission + package, ~0.3s
python tools/submit.py --emit-only       # just rewrite the .txt files
```

### Submitting

`python tools/submit.py` rewrites every submission from `best/` and rebuilds
`dist/hackit.zip`. It does no solving, so it takes ~0.3s, and ~0.05s when
nothing changed.

Two reasons that speed matters:

- **The format is wrong at 12:00.** Fix `Plan.to_submission_text()` — the only
  place that knows the format — and re-emit. One edit, no re-solve.
- **It verifies three things that go stale silently.** The plan hash (is
  `best/` in sync with `best-known.json`?), the components (does the engine
  still produce the same facts?), and the score (does the *current* formula
  still give the recorded total?). That last one fires the moment
  `fit.py --write` lands a real formula: every score recorded under the
  placeholder is fiction, and `stale` is how you find out before the
  leaderboard tells you.

The zip is byte-identical for identical content — fixed timestamps, sorted
entries — so an unchanged rebuild is detected and skipped. `levels/` and
`docs/` are excluded: the organisers' material is not yours to redistribute
and it is most of the bytes. `--include-inputs` if you need them.

### Multi-seed search

Every (level, planner) is solved `--seeds N` times from independent
seeds, in parallel, and only the best replicate survives. N defaults to
the core count.

**Watch the SPREAD column, and act on it.** It is the gap between the
best and worst seed:

- **Wide spread** — the planner is luck-dependent. More seeds pay, and a
  long budget on one seed is worse than the same time split across
  several. Raise `--seeds`.
- **Zero spread** — the planner is deterministic and every extra core did
  identical work. `--seeds 1` and spend the cores on `--time` instead.
  run.py says so explicitly at the end of the run.

**Wall clock is no longer roughly `--time`.** With more tasks than cores
the run takes ceil(tasks/cores) waves. The banner prints the estimate;
if it says 240s, background it.

The winning seed goes into `best-known.json` as `seed`, with
`base_seed`, `seed_index` and `seeds_tried` beside it — enough to
regenerate that exact plan. `spread` is in the history too, so you can
see whether a level has stopped responding to seeds as well as whether
it has stopped climbing.

### Local search — where the score actually comes from

`src/planners/anneal.py` warm-starts from `best/level{N}.json` and improves
it. This compounds: every run picks up where the last one stopped, so
re-running the same command keeps climbing. Two functions are yours:

- **`_mutate(plan, rng)`** — a neighbouring plan. This is the highest-value
  code in the repo. Moves that respect the problem's structure (swap two
  stops, move one item between bins) beat random perturbation by more than
  any metaheuristic tuning will.
- **`_initial(ctx)`** — a constructive start. Search improves what you give
  it; it does not rescue it.

`climb` (strictly uphill) and `anneal` (accepts downhill moves early) share
both functions, so you get the comparison for free. **Run `climb` first.** If
it wins, the landscape is smooth and your time belongs in `_mutate`, not in
the temperature schedule.

**When evaluation rate becomes the bottleneck — and it will — go
incremental.** `PlanMutationProblem` re-simulates every candidate, which is
~10³ evals/sec; a `SearchProblem` that maintains its score across a move is
~10⁵. That ratio is score. The interface is already in `search.py`.

**Then immediately run `python tools/check_incremental.py --planner anneal`.**
An incremental score that drifts from the full replay does not crash — the
search just climbs a hill that is not there, and the leaderboard tells you at
14:00. The checker applies random moves, compares against full replay after
each, and verifies undo restores state exactly. It is `diff_trace.py` for the
fast path. Re-run it every time you add a move type.

### Is that change real? `--compare`

```bash
python tools/run.py --compare climb,anneal --seeds 8
```

Runs both planners on **identical** seeds and reports per-seed differences
with an exact sign test. Paired is the point: comparing means across
independent seeds buries a real effect under seed variance.

- **4 seeds can never reach p≤0.05** — best possible is 0.125. run.py says so
  before starting. **6 seeds** is the minimum for a verdict.
- `inconclusive` means the seeds disagree, i.e. the difference is inside the
  noise. It does not mean there is no difference.
- A search stopped by the clock rather than an iteration cap did a
  machine-dependent number of iterations, so the verdict is not exactly
  reproducible. run.py flags this.

Use it before you keep a planner change. A +200k move against a 30M spread is
not evidence, and chasing one is a reliable way to lose two hours.

Stdlib only. No install step, no virtualenv, nothing to break at 10:05.

## First twenty minutes

1. Spec PDF into `docs/`, level files into `levels/`.
2. `python tools/gen_probes.py` → upload the empty plans → every level on the board.
3. Tell me the action types and the scoring rules. Then `Plan.to_submission_text()`,
   `parse_level()`, and `engine/simulate.py:_real`, in that order.
4. Delete `src/planners/placeholder.py`.
5. Then `_mutate` in `src/planners/anneal.py`, and leave `climb` running.
