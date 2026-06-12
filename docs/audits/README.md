# BAW System Audits — Index

Historical audit reports of the BAW agent platform. All audits are point-in-time
snapshots of findings; treat as evidence, not current truth. Re-run `baw --doctor`
for live status.

## System Audits (phase-by-phase)

- `BAW_SYSTEM_AUDIT.md` — 2026-06-11 — Initial whole-system pass
- `BAW_SYSTEM_AUDIT_P2.md` — 2026-06-11 — Phase 2 deep-dive
- `BAW_SYSTEM_AUDIT_P3.md` — 2026-06-11 — Phase 3 deep-dive
- `BAW_SYSTEM_AUDIT_P4_v2.md` — 2026-06-12 — Phase 4 v2 (most recent overview)

## Targeted Audits (single-concern)

- `BAW_AUDIT_OPUS4.8.md` — 2026-06-12 — Opus 4.8 reasoning audit (P0 criticals)
- `BAW_AUDIT_OPUS4.8_VERIFY.md` — 2026-06-12 — Verification round on Opus findings
- `BAW_AUDIT_FABLE5_COURT_V2.md` — 2026-06-12 — Court subsystem under Fable 5 model
- `BAW_AUDIT_COURT.md` — 2026-06-12 — Court subsystem baseline
- `BAW_AUDIT_CODE.md` — 2026-06-12 — Code-level bugs / refactor list
- `BAW_AUDIT_UX.md` — 2026-06-12 — UX / Telegram interaction audit

## How audits were moved here

Originally these files sat loose in the repo root. On 2026-06-12 they were
relocated to `docs/audits/` with `git mv` so history is preserved. Re-run
`git log --follow docs/audits/<file>` to see the move commit.

## Running a new audit

1. `baw --diagnostics` — quick health check
2. `baw --doctor` — auto-repair pass
3. `baw --cfg check` — config schema validation
4. For a full deep audit, copy the latest P* template and re-run with current state
