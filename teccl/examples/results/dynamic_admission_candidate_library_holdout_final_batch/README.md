# Final predeclared frozen-library holdout batch

This directory contains the final seven InterDC8 `lambda=0.06` holdout seeds
`20260821`--`20260827`. The five-tree candidate library and all solver settings
were frozen before their traces were generated. No further seed is added to
this experiment after observing the results.

| seed | early DPP (A/B/U) | DDPP (A/B/U) |
| ---: | ---: | ---: |
| 20260821 | 7/1/0 | 8/0/0 |
| 20260822 | 5/3/0 | 7/1/0 |
| 20260823 | 6/2/0 | 7/1/0 |
| 20260824 | 7/1/0 | 8/0/0 |
| 20260825 | 4/4/0 | 7/1/0 |
| 20260826 | 6/2/0 | 7/1/0 |
| 20260827 | 7/1/0 | 8/0/0 |
| batch total | 42/14/0 | 52/4/0 |

DDPP has more certified admissions on all seven paired seeds. All accepted
schedules, certificate hashes, ledgers, trace hashes, candidate-library hashes,
and implementation fingerprints passed independent replay.

Combining this predeclared batch with the earlier unseen seeds
`20260812`--`20260820` gives 16 paired seeds and 128 requests per policy:

- early DPP: `108/20/0`;
- DDPP: `123/4/1`;
- early-DPP blocking rate: `15.625%`;
- DDPP blocking interval: `3.125%`--`3.90625%`;
- paired seed outcomes: 12 DDPP wins, 4 ties, 0 losses;
- mean certified-admission gain: `0.9375` requests per eight-request trace;
- 95% paired-mean t interval: `[0.5262, 1.3488]`;
- two-sided exact sign-test value: `0.00048828125`.

The DDPP blocking upper bound pessimistically counts the sole unknown as
blocked. This completes the seed-expansion stage for this load; continuing to
add seeds after seeing significance would violate the predeclared stopping
rule.
