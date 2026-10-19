# Research continuation

Read the latest main `ROADMAP_CONSTITUTION_V3.json` before research changes.
Use the existing canonical controller, research engine, final validators and
AWS receiver. Do not create a substitute research or release pipeline.

Three-frame compute and bounded continuous research are now on main;
`research-speed` mirrors the development checkpoint. Before continuing, inspect
latest main and the current Actions results. Read its `B_BOT_MASTER_HANDOFF.json` / `research_development` node and
the hash-bound `strategy_lab/research_development_state.json`. The handoff
is produced by `strategy_lab/master_handoff.py`; README notes are navigation,
not a replacement for the authoritative JSON. Development status is not live
runtime evidence. Preserve existing changes and update the official handoff
and commit reviewable checkpoints when completing a work stage.

Authorized scope: three distinct timeframe compute workers, bounded longer
research runs, intermediate durable checkpoints, duplicate-safe recovery,
and basic/genetic research allocation. This is not yet production complete.
State/ledger writes and final/blind validation remain coordinated. Read the
pending items before treating any draft as finished or merging it.

Do not change operating orders, leverage, tiers, final qualification gates,
or server registration conditions. Do not cancel running main research,
disable its schedule, clear research-state, reset trial history, or reuse
sealed blind evidence to simplify development. Chat interruption must not
be coupled to GitHub job cancellation. Keep unverified changes off main.

Recovery baseline: main commit `63aea437b46d1cc501fa63f1aadb281df5bf8bd0`
fixes the exact-version trial reservation crash. Actual research run
`39114847248` completed eight cycles; engineering run `39114847224` passed
209 tests. Checkpoint and candidate upload succeeded; exported candidates
were zero. These are dated observations, not a guarantee of future health.

Latest verified milestone: main `3c96b819`, research `39187205002` completed
eight cycles including two three-worker batches. Engineering `39187204941`
passed 219 tests and six canonical verifiers, with 37 runtime files unchanged.
AWS received its candidate commit `47f33c22f05aaaf3ae0d5532eabde7a71049d595`;
exported candidates remained zero. Follow-up `08fa0b4` restores saved grammar training dates. Research
`39187460325` completed eight cycles with three three-worker batches; engineering
`39187460344` passed 220 tests and all six verifiers. Use the authoritative
development node for the latest AWS receipt and remaining work.
Long scheduled execution, genetic/meta allocation, new exit/reentry parity and
server rejection/lock diagnosis are not implied complete by this milestone.
