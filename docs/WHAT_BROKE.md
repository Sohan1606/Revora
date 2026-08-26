# What Broke — genuine engineering incidents from REVORA's build

For the submission form's "what broke and how you got out." Every incident below is
real, reproducible from git history and the test suite. No invented war stories.

---

## 1. The shipped ML model silently never served a single prediction

**Problem.** After packaging, every recovery decision fell back to the cold-start
prior (0.30 for everything) even though a trained, calibrated XGBoost artifact was
present and loaded successfully.

**Root cause.** The provider refuses to predict actions the model wasn't trained on
(an anti-extrapolation gate, by design). The gate reads `metadata["actions"]` —
but the training script never recorded that field. The artifact loaded fine and
then quietly failed the gate for every action. Unit tests passed because they
constructed metadata by hand, with the field present — the integration between
trainer and provider was never tested end-to-end.

**Fix.** `train_model` now records the exact set of trained actions into the
artifact; the production artifact was retrained and now serves `model:` estimates
for seen actions and falls back for unseen ones.

**Regression test.** `tests/test_serving_gate_regression.py` — trains a real
mini-artifact through the actual trainer → provider path and asserts the basis is
`model:` for seen actions and NOT for unseen ones.

**Lesson.** "It loads" is not "it serves." Test the artifact contract end-to-end,
not the components in isolation. Silent fallbacks need observability — every
decision now records which basis produced its estimates.

## 2. Two running experiments broke every subsequent decision (500s)

**Problem.** During browser verification, the Demo Lab started returning 500s.
The traceback ended in `MultipleResultsFound` deep inside the decision path.

**Root cause.** `get_running_experiment()` used `scalar_one_or_none()`, and the
API allowed starting a second experiment while one was running. Two concurrent
"running" rows → every `decide()` call raised. A concurrency invariant existed in
our heads, not in the code.

**Fix.** `start` now refuses while another experiment is running (API → 409 with
the conflicting name), and the query is robust to any legacy duplicate rows
(latest `started_at` wins).

**Regression test.** `tests/test_experiment_invariants.py` — asserts the 409
guard and that duplicate running rows can't crash the lookup.

**Lesson.** State machines for *entities* (cases) weren't enough; the *system*
also has lifecycle invariants (one active experiment per merchant) that need
explicit enforcement and tests.

## 3. Environment traps that cost real debugging time (all fixed, all real)

- **A prefilled Postgres `DATABASE_URL` in `.env.example`** made the first local
  setup hang for a user with no Postgres — connection attempts never timed out.
  Fix: empty by default (SQLite fallback) + a 5-second connect timeout so an
  unreachable database fails fast with a clear error.
- **Windows/PowerShell encoding of an appended env secret** produced a `.env`
  the server couldn't parse, surfacing as opaque 503s at sign-in. Fix: all
  environment setup now happens inside Python (encoding-safe), and the one-command
  launcher generates secrets itself.
- **Empty boolean env lines** (`AUTO_CREATE_TABLES=`) crashed Settings on parse.
  Fix: tolerant validator (empty → false).
- **An XGBoost artifact trained on one OS/build refused to load on another**
  ("input stream corrupted"). Fix: pin the ML stack versions in requirements, and
  harden the provider so an unusable artifact can never crash decisions — it logs
  one warning, poison-caches, and falls back to empirical estimates (tested in
  `tests/test_ml_resilience.py`).

**Lesson.** For a judged project, the unboxing experience is part of the product.
We now verify the *shipped artifact* end-to-end (extract → install → launch →
browser test), not just the working tree.

---

*All incidents above are traceable: fixes are in git history; regression tests run
in the 116-test suite.*
