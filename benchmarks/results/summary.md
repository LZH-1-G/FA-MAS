# FA-MAS benchmark summary

468 runs (deterministic simulation, mock LLM).

## Overall (all scenarios)

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls |
|---|---:|---:|---:|---:|---:|---:|
| A: single agent | 73% | 88% | 71.1s | 8,370 | $0.0420 | 1.73 |
| B: fixed workflow | 88% | 88% | 67.3s | 14,587 | $0.0169 | 4.91 |
| C: full replan | 94% | 96% | 85.8s | 19,774 | $0.0483 | 12.49 |
| D: naive retry | 73% | 76% | 55.4s | 11,971 | $0.0327 | 8.37 |
| FA-MAS | 97% | 100% | 59.4s | 12,245 | $0.0312 | 7.95 |
| FA-MAS+audit | 97% | 100% | 59.7s | 13,174 | $0.0311 | 7.77 |

## Under failure (all scenarios except `none`)

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls |
|---|---:|---:|---:|---:|---:|---:|
| A: single agent | 71% | 88% | 73.6s | 8,641 | $0.0434 | 1.79 |
| B: fixed workflow | 88% | 88% | 69.1s | 15,069 | $0.0171 | 5.06 |
| C: full replan | 93% | 96% | 89.0s | 20,574 | $0.0500 | 13.00 |
| D: naive retry | 71% | 74% | 56.1s | 12,121 | $0.0331 | 8.54 |
| FA-MAS | 97% | 100% | 60.5s | 12,418 | $0.0315 | 8.08 |
| FA-MAS+audit | 97% | 100% | 60.8s | 13,424 | $0.0314 | 7.89 |

## Recovery behaviour (scenarios with failures)

| System | Recovery success | Reuse rate | Invalid reuse | Redundant exec. | Wasted tokens | Replan overhead (tok) |
|---|---:|---:|---:|---:|---:|---:|
| A: single agent | 55% | – | – | 0.00 | 4,221 | 0 |
| B: fixed workflow | 80% | 0% | – | 0.86 | 7,159 | 0 |
| C: full replan | 92% | 0% | – | 4.97 | 8,836 | 1,804 |
| D: naive retry | 62% | 100% | 10% | 0.00 | 3,358 | 0 |
| FA-MAS | 98% | 96% | 2% | 0.00 | 1,849 | 309 |
| FA-MAS+audit | 98% | 96% | 0% | 0.00 | 1,664 | 1,501 |

## Scenario `none` — No failures

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls | Reuse | Redundant |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A: single agent | 100% | 100% | 41.7s | 5,123 | $0.0253 | 1.00 | – | 0.00 |
| B: fixed workflow | 100% | 100% | 46.1s | 8,808 | $0.0147 | 3.17 | – | 0.00 |
| C: full replan | 100% | 100% | 46.7s | 10,172 | $0.0275 | 6.33 | – | 0.00 |
| D: naive retry | 100% | 100% | 46.7s | 10,172 | $0.0275 | 6.33 | – | 0.00 |
| FA-MAS | 100% | 100% | 46.7s | 10,172 | $0.0275 | 6.33 | – | 0.00 |
| FA-MAS+audit | 100% | 100% | 46.7s | 10,172 | $0.0275 | 6.33 | – | 0.00 |

## Scenario `transient` — Transient timeout

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls | Reuse | Redundant |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A: single agent | 100% | 100% | 61.7s | 6,323 | $0.0283 | 2.00 | – | 0.00 |
| B: fixed workflow | 100% | 100% | 66.1s | 10,008 | $0.0149 | 4.17 | – | 0.00 |
| C: full replan | 100% | 100% | 80.1s | 15,242 | $0.0377 | 9.17 | 0% | 1.83 |
| D: naive retry | 100% | 100% | 65.5s | 11,372 | $0.0281 | 7.33 | 100% | 0.00 |
| FA-MAS | 100% | 100% | 66.1s | 11,542 | $0.0282 | 7.33 | 100% | 0.00 |
| FA-MAS+audit | 100% | 100% | 66.8s | 12,211 | $0.0283 | 7.33 | 100% | 0.00 |

## Scenario `agent_failure` — Incapable agent

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls | Reuse | Redundant |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A: single agent | 0% | 0% | 162.6s | 19,644 | $0.1009 | 4.00 | – | 0.00 |
| B: fixed workflow | 0% | 0% | 84.7s | 27,087 | $0.0078 | 8.00 | 0% | 3.00 |
| C: full replan | 100% | 100% | 74.2s | 18,284 | $0.0453 | 12.17 | 0% | 4.33 |
| D: naive retry | 83% | 83% | 61.6s | 12,495 | $0.0353 | 8.50 | 100% | 0.00 |
| FA-MAS | 100% | 100% | 57.0s | 11,576 | $0.0325 | 7.83 | 100% | 0.00 |
| FA-MAS+audit | 100% | 100% | 58.5s | 13,278 | $0.0327 | 7.83 | 100% | 0.00 |

## Scenario `complex_task` — Task too complex

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls | Reuse | Redundant |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A: single agent | 50% | 50% | 142.5s | 15,899 | $0.0855 | 2.50 | – | 0.00 |
| B: fixed workflow | 50% | 50% | 43.9s | 14,865 | $0.0063 | 3.50 | – | 0.00 |
| C: full replan | 100% | 100% | 68.0s | 16,722 | $0.0380 | 10.50 | 0% | 2.17 |
| D: naive retry | 50% | 50% | 39.9s | 9,481 | $0.0224 | 5.67 | 100% | 0.00 |
| FA-MAS | 100% | 100% | 48.3s | 11,460 | $0.0277 | 7.67 | 100% | 0.00 |
| FA-MAS+audit | 100% | 100% | 49.0s | 12,298 | $0.0279 | 7.67 | 100% | 0.00 |

## Scenario `corruption` — Silent upstream corruption

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls | Reuse | Redundant |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A: single agent | 0% | 100% | 41.7s | 5,123 | $0.0253 | 1.00 | – | 0.00 |
| B: fixed workflow | 100% | 100% | 66.9s | 15,510 | $0.0167 | 5.17 | 0% | 0.00 |
| C: full replan | 83% | 100% | 70.4s | 18,191 | $0.0406 | 11.33 | 0% | 1.83 |
| D: naive retry | 0% | 17% | 53.4s | 15,949 | $0.0518 | 13.83 | 100% | 0.00 |
| FA-MAS | 83% | 100% | 56.4s | 14,284 | $0.0310 | 9.50 | 69% | 0.00 |
| FA-MAS+audit | 83% | 100% | 57.1s | 14,994 | $0.0311 | 9.50 | 69% | 0.00 |

## Scenario `missing_dependency` — Planner omits a dependency

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls | Reuse | Redundant |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A: single agent | 100% | 100% | 41.7s | 5,123 | $0.0253 | 1.00 | – | 0.00 |
| B: fixed workflow | 100% | 100% | 46.1s | 8,808 | $0.0147 | 3.17 | – | 0.00 |
| C: full replan | 100% | 100% | 68.4s | 17,391 | $0.0402 | 11.33 | 0% | 4.17 |
| D: naive retry | 17% | 17% | 43.3s | 10,078 | $0.0239 | 8.83 | 100% | 0.00 |
| FA-MAS | 100% | 100% | 50.3s | 10,947 | $0.0277 | 7.17 | 100% | 0.00 |
| FA-MAS+audit | 100% | 100% | 51.7s | 12,559 | $0.0280 | 7.17 | 100% | 0.00 |

## Scenario `late_failure` — Late failure in report writing

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls | Reuse | Redundant |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A: single agent | 100% | 100% | 82.0s | 9,963 | $0.0505 | 2.00 | – | 0.00 |
| B: fixed workflow | 100% | 100% | 90.8s | 17,334 | $0.0294 | 6.33 | 0% | 2.17 |
| C: full replan | 100% | 100% | 92.7s | 20,322 | $0.0551 | 12.67 | 0% | 5.33 |
| D: naive retry | 100% | 100% | 70.4s | 12,658 | $0.0418 | 7.33 | 100% | 0.00 |
| FA-MAS | 100% | 100% | 71.0s | 12,840 | $0.0418 | 7.33 | 100% | 0.00 |
| FA-MAS+audit | 100% | 100% | 72.9s | 14,929 | $0.0421 | 7.33 | 100% | 0.00 |

## Scenario `cascade` — Cascade of failures

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls | Reuse | Redundant |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A: single agent | 0% | 100% | 61.7s | 6,323 | $0.0283 | 2.00 | – | 0.00 |
| B: fixed workflow | 100% | 100% | 131.6s | 25,236 | $0.0315 | 9.33 | 0% | 2.17 |
| C: full replan | 83% | 100% | 124.4s | 25,020 | $0.0640 | 15.33 | 0% | 6.33 |
| D: naive retry | 0% | 17% | 72.2s | 17,149 | $0.0524 | 14.83 | 100% | 0.00 |
| FA-MAS | 83% | 100% | 98.7s | 18,009 | $0.0449 | 11.33 | 92% | 0.00 |
| FA-MAS+audit | 83% | 100% | 91.3s | 17,532 | $0.0420 | 9.00 | 88% | 0.00 |

## Scenario `stochastic` — Stochastic failures

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls | Reuse | Redundant |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A: single agent | 100% | 100% | 57.8s | 7,059 | $0.0354 | 1.40 | – | 0.00 |
| B: fixed workflow | 100% | 100% | 59.8s | 12,395 | $0.0168 | 4.20 | 0% | 0.60 |
| C: full replan | 90% | 90% | 98.1s | 23,142 | $0.0559 | 14.70 | 0% | 6.73 |
| D: naive retry | 100% | 100% | 53.4s | 11,253 | $0.0284 | 7.23 | 100% | 0.00 |
| FA-MAS | 100% | 100% | 55.6s | 11,672 | $0.0288 | 7.77 | 100% | 0.00 |
| FA-MAS+audit | 100% | 100% | 56.4s | 12,658 | $0.0290 | 7.77 | 100% | 0.00 |

## Complexity: simple

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls |
|---|---:|---:|---:|---:|---:|---:|
| A: single agent | 77% | 92% | 15.6s | 2,841 | $0.0101 | 1.62 |
| B: fixed workflow | 92% | 92% | 17.9s | 4,313 | $0.0045 | 4.62 |
| C: full replan | 85% | 100% | 19.5s | 3,436 | $0.0105 | 2.08 |
| D: naive retry | 77% | 92% | 17.8s | 3,169 | $0.0114 | 1.62 |
| FA-MAS | 85% | 100% | 18.2s | 3,120 | $0.0098 | 1.92 |
| FA-MAS+audit | 85% | 100% | 18.3s | 3,149 | $0.0098 | 1.92 |

## Complexity: medium

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls |
|---|---:|---:|---:|---:|---:|---:|
| A: single agent | 77% | 92% | 27.8s | 4,696 | $0.0182 | 1.62 |
| B: fixed workflow | 92% | 92% | 31.0s | 7,679 | $0.0079 | 4.62 |
| C: full replan | 100% | 100% | 44.5s | 9,718 | $0.0173 | 7.65 |
| D: naive retry | 77% | 77% | 34.2s | 6,647 | $0.0134 | 6.12 |
| FA-MAS | 100% | 100% | 33.9s | 6,740 | $0.0122 | 5.62 |
| FA-MAS+audit | 100% | 100% | 33.9s | 6,934 | $0.0122 | 5.50 |

## Complexity: complex

| System | Correct | Completed | Latency | Tokens | Cost | Agent calls |
|---|---:|---:|---:|---:|---:|---:|
| A: single agent | 69% | 85% | 118.4s | 12,663 | $0.0685 | 1.85 |
| B: fixed workflow | 85% | 85% | 108.0s | 22,618 | $0.0271 | 5.21 |
| C: full replan | 92% | 92% | 135.4s | 31,923 | $0.0815 | 19.18 |
| D: naive retry | 69% | 69% | 82.0s | 18,454 | $0.0527 | 12.13 |
| FA-MAS | 100% | 100% | 90.1s | 18,957 | $0.0510 | 11.51 |
| FA-MAS+audit | 100% | 100% | 90.7s | 20,676 | $0.0508 | 11.23 |
