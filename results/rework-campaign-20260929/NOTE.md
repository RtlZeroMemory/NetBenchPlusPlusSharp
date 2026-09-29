# Stopped campaign, kept as "before C# optimization" evidence

Started 2026-09-29 with the frozen binaries in `results/audit-checks/frozen-binaries.txt` (C++ `37338867…`, C# `9baa04d7…`). Stopped deliberately at trial 158 of about 290, with zero failures, to optimize the C# parser. Its C# measurements were then obsolete for the final comparison.

Completed phases: processing-only controls, EASY saturation, HARD saturation, controlled comparisons, EASY paced (all complete), and part of HARD sustained. `report.md` in this folder was generated from these trials. The declared campaign was rerun with the optimized binaries in `results/final-campaign-20260929`. Diagnostics for the optimization are in `results/diagnostics-csharp-parser-20260929`.
