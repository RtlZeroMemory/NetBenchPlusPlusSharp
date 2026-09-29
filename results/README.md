# Published results

Raw data behind the numbers in the [README](../README.md) and the [full results](../docs/benchmarks/results.md).

Each campaign folder has the generated `report.md`, one row per trial in `trials.csv`, the machine and build details in `environment.json`, and every trial's complete client and server output in `results.json.gz`. The per-trial folders the runner writes locally are left out, because `results.json.gz` holds the same data. To rebuild a report from it:

```powershell
gzip -dk results/final-campaign-20260929/results.json.gz
python bench/report.py results/final-campaign-20260929
```

| Folder | What it is |
| --- | --- |
| [final-campaign-20260929](final-campaign-20260929) | **The final results.** 270 trials, 0 failures, 254 eligible |
| [rework-campaign-20260929](rework-campaign-20260929) | The same campaign before the C# optimization, stopped at trial 158 ([note](rework-campaign-20260929/NOTE.md)) |
| [rework-core-20260929](rework-core-20260929) | An invalid first attempt, where a C# parser bug rejected valid data. Kept as evidence ([note](rework-core-20260929/NOTE.md)) |
| [diagnostics-csharp-parser-20260929](diagnostics-csharp-parser-20260929) | A/B tests and ablations for the C# optimization ([summary](diagnostics-csharp-parser-20260929/README.md)) |
| [diagnostics-baseline-20260929](diagnostics-baseline-20260929) | Diagnostics on the first-pass binaries: client CPU headroom, 1 MiB frames and windows, pause controls, processing-only A/B |
| [diagnostics-rework-20260929](diagnostics-rework-20260929) | Diagnostics on the reworked binaries: TCP stalls, large JSON frames, fairness, client headroom |
| [diagnostics-fairness-20260929](diagnostics-fairness-20260929) | Connection-fairness experiments behind the per-frame requeue |
| [audit-checks](audit-checks) | Test-suite logs (Release and AddressSanitizer), oracle checks on the campaign data, frozen binary hashes |
| [audit-baseline-20260929](audit-baseline-20260929) | Hashes of the pre-audit sources and binaries. The sources themselves are the first commit in this repository |
| [first-benchmark-20260929](first-benchmark-20260929) | The first benchmark pass ([write-up](../docs/benchmarks/first-pass.md)), superseded |

The generated corpora (0.25-1 GiB each) aren't published. `tools/corpus.py` rebuilds them byte for byte from the seeds in each `environment.json`, and the runner does this automatically.
