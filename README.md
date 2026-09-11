# hackit

Scaffold for a five-hour optimisation hackathon. Problem-agnostic: the
simulator, the level parser and the submission format are stubs, filled in
once the briefing lands.

Stdlib only, Python 3.10+. No install step.

```bash
python tools/run.py --time 3     # solve, score, compare, keep winners
python tools/fit.py              # recover the scoring formula from logs
python tools/selftest.py         # check the solver and the record store
```

The separation the whole thing rests on:

> **engine computes facts · scoring applies weights · planners decide**

When the portal disagrees with a local score, that tells you which of the three
to open. See [CLAUDE.md](CLAUDE.md) for the working order, the file map and the
rules that matter under time pressure.
