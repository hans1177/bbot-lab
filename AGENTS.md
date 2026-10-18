# Research continuation

Read the latest main `ROADMAP_CONSTITUTION_V3.json` before research changes.
Use the existing canonical controller, research engine, final validators and
AWS receiver. Do not create a substitute research or release pipeline.

An unfinished research speed improvement is preserved on branch
`research-speed`. Before continuing it, fetch/inspect that branch and latest
main. Read its `B_BOT_MASTER_HANDOFF.json` / `research_development` node and
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
