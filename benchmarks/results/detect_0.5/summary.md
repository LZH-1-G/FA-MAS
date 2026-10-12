# FA-MAS benchmark summary

400 runs (deterministic simulation, mock LLM).

## Overall (all scenarios)

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls |
|---|---:|---:|---:|---:|---:|---:|
| C: full replan | 90% | 100% | 110.0s | 24,585 | $0.0597 | 15.34 |
| D: naive retry | 0% | 86% | 83.8s | 16,085 | $0.0442 | 11.29 |
| FA-MAS | 80% | 100% | 86.6s | 18,034 | $0.0434 | 11.78 |
| FA-MAS+audit | 85% | 100% | 85.9s | 19,155 | $0.0427 | 11.18 |

## Under failure (all scenarios except `none`)

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls |
|---|---:|---:|---:|---:|---:|---:|
| C: full replan | 90% | 100% | 110.0s | 24,585 | $0.0597 | 15.34 |
| D: naive retry | 0% | 86% | 83.8s | 16,085 | $0.0442 | 11.29 |
| FA-MAS | 80% | 100% | 86.6s | 18,034 | $0.0434 | 11.78 |
| FA-MAS+audit | 85% | 100% | 85.9s | 19,155 | $0.0427 | 11.18 |

## Recovery behaviour (scenarios with failures)

| System | Recovery success | Reuse rate | Invalid reuse | Redundant exec. | Wasted tokens | Replan overhead (tok) |
|---|---:|---:|---:|---:|---:|---:|
| C: full replan | 100% | 0% | – | 4.75 | 10,868 | 1,878 |
| D: naive retry | 0% | 100% | 55% | 0.00 | 4,872 | 0 |
| FA-MAS | 89% | 77% | 13% | 0.00 | 5,561 | 582 |
| FA-MAS+audit | 94% | 77% | 7% | 0.00 | 4,985 | 2,289 |

## Scenario `corruption` — Silent upstream corruption

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls | Reuse | Redundant |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| C: full replan | 80% | 100% | 77.6s | 19,927 | $0.0452 | 12.68 | 0% | 1.90 |
| D: naive retry | 0% | 86% | 63.5s | 14,407 | $0.0377 | 10.36 | 100% | 0.00 |
| FA-MAS | 80% | 100% | 63.2s | 15,925 | $0.0353 | 10.78 | 51% | 0.00 |
| FA-MAS+audit | 80% | 100% | 63.9s | 16,680 | $0.0354 | 10.78 | 51% | 0.00 |

## Scenario `cascade` — Cascade of failures

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls | Reuse | Redundant |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| C: full replan | 100% | 100% | 142.5s | 29,243 | $0.0743 | 18.00 | 0% | 7.60 |
| D: naive retry | 0% | 86% | 104.0s | 17,763 | $0.0508 | 12.22 | 100% | 0.00 |
| FA-MAS | 80% | 100% | 110.1s | 20,143 | $0.0515 | 12.78 | 85% | 0.00 |
| FA-MAS+audit | 90% | 100% | 108.0s | 21,630 | $0.0499 | 11.58 | 87% | 0.00 |

## Complexity: medium

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls |
|---|---:|---:|---:|---:|---:|---:|
| C: full replan | 80% | 100% | 56.5s | 11,490 | $0.0202 | 8.82 |
| D: naive retry | 0% | 100% | 47.0s | 7,574 | $0.0164 | 6.55 |
| FA-MAS | 60% | 100% | 45.7s | 8,535 | $0.0145 | 7.05 |
| FA-MAS+audit | 68% | 100% | 45.8s | 9,081 | $0.0145 | 7.00 |

## Complexity: complex

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls |
|---|---:|---:|---:|---:|---:|---:|
| C: full replan | 97% | 100% | 145.7s | 33,315 | $0.0861 | 19.68 |
| D: naive retry | 0% | 77% | 108.3s | 21,759 | $0.0628 | 14.45 |
| FA-MAS | 93% | 100% | 113.9s | 24,367 | $0.0627 | 14.93 |
| FA-MAS+audit | 97% | 100% | 112.6s | 25,871 | $0.0615 | 13.97 |
