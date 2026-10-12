# FA-MAS benchmark summary

400 runs (deterministic simulation, mock LLM).

## Overall (all scenarios)

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls |
|---|---:|---:|---:|---:|---:|---:|
| C: full replan | 95% | 100% | 111.6s | 25,034 | $0.0605 | 15.55 |
| D: naive retry | 0% | 38% | 73.3s | 17,309 | $0.0480 | 14.09 |
| FA-MAS | 90% | 100% | 88.0s | 18,473 | $0.0437 | 12.00 |
| FA-MAS+audit | 94% | 100% | 85.6s | 19,161 | $0.0424 | 11.03 |

## Under failure (all scenarios except `none`)

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls |
|---|---:|---:|---:|---:|---:|---:|
| C: full replan | 95% | 100% | 111.6s | 25,034 | $0.0605 | 15.55 |
| D: naive retry | 0% | 38% | 73.3s | 17,309 | $0.0480 | 14.09 |
| FA-MAS | 90% | 100% | 88.0s | 18,473 | $0.0437 | 12.00 |
| FA-MAS+audit | 94% | 100% | 85.6s | 19,161 | $0.0424 | 11.03 |

## Recovery behaviour (scenarios with failures)

| System | Recovery success | Reuse rate | Invalid reuse | Redundant exec. | Wasted tokens | Replan overhead (tok) |
|---|---:|---:|---:|---:|---:|---:|
| C: full replan | 100% | 0% | – | 4.85 | 11,160 | 2,041 |
| D: naive retry | 0% | 100% | 46% | 0.00 | 8,790 | 0 |
| FA-MAS | 95% | 83% | 10% | 0.00 | 5,813 | 779 |
| FA-MAS+audit | 99% | 81% | 3% | 0.00 | 4,887 | 2,405 |

## Scenario `corruption` — Silent upstream corruption

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls | Reuse | Redundant |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| C: full replan | 90% | 100% | 80.6s | 20,825 | $0.0466 | 13.10 | 0% | 2.10 |
| D: naive retry | 0% | 38% | 60.6s | 16,383 | $0.0460 | 13.40 | 100% | 0.00 |
| FA-MAS | 90% | 100% | 64.6s | 16,369 | $0.0356 | 11.00 | 61% | 0.00 |
| FA-MAS+audit | 90% | 100% | 65.4s | 17,202 | $0.0357 | 11.00 | 61% | 0.00 |

## Scenario `cascade` — Cascade of failures

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls | Reuse | Redundant |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| C: full replan | 100% | 100% | 142.5s | 29,243 | $0.0743 | 18.00 | 0% | 7.60 |
| D: naive retry | 0% | 38% | 86.0s | 18,236 | $0.0500 | 14.78 | 100% | 0.00 |
| FA-MAS | 90% | 100% | 111.4s | 20,578 | $0.0517 | 13.00 | 89% | 0.00 |
| FA-MAS+audit | 98% | 100% | 105.9s | 21,121 | $0.0491 | 11.06 | 89% | 0.00 |

## Complexity: medium

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls |
|---|---:|---:|---:|---:|---:|---:|
| C: full replan | 88% | 100% | 57.6s | 11,858 | $0.0206 | 9.10 |
| D: naive retry | 0% | 70% | 49.0s | 8,006 | $0.0178 | 7.45 |
| FA-MAS | 75% | 100% | 46.4s | 8,939 | $0.0145 | 7.45 |
| FA-MAS+audit | 85% | 100% | 45.2s | 9,255 | $0.0144 | 7.08 |

## Complexity: complex

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls |
|---|---:|---:|---:|---:|---:|---:|
| C: full replan | 100% | 100% | 147.5s | 33,818 | $0.0870 | 19.85 |
| D: naive retry | 0% | 17% | 89.5s | 23,511 | $0.0681 | 18.52 |
| FA-MAS | 100% | 100% | 115.7s | 24,830 | $0.0631 | 15.03 |
| FA-MAS+audit | 100% | 100% | 112.5s | 25,766 | $0.0611 | 13.67 |
