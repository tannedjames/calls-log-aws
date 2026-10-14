# PRD quality review: calls-log on AWS

Rubric: `.claude/skills/bmad-prd/assets/prd-validation-checklist.md`. Reviewed `prd.md` and
`addendum.md` on 2026-10-14, after the Owner's corrections (A5, A10) and input reconciliation.
Run inline (no subagents).

## Overall verdict

The PRD has a clear thesis: the same results, processed per upload, for ≤ $2, with the PC kept as
an alternative. Its metrics, counter-metrics and accepted deviations follow from that thesis, and
most FRs carry testable consequences. The risk sits in a few done-ness gaps:
- FR-11 and FR-15 have no testable consequence;
- FR-3 offers two alternative behaviours;
- the Folder Status states don't match FR-9.

These would leave story writers guessing. All are one-line fixes.

## Decision-readiness: strong

Decisions are stated as decisions, with what was given up:
- "Accepted deviation from today" in FR-2 and FR-12;
- the accepted risk ADV-17 in §9;
- counter-metrics SM-C1 to SM-C3.

The Open Questions are genuinely open and routed to an owner and step.

### Findings
- **medium** Both writers can race on tab creation (§9 accepted risk, FR-29). The accepted risk
  covers last-writer-wins, but not two systems creating the same day tab at the same moment.
  FR-6 only serialises Runs within AWS. *Fix:* name the duplicate-tab case in the accepted risk,
  with the runbook rule that one person-day is processed by one system at a time.

## Substance over theater: strong

The NFRs carry thresholds: 30 min switch-back, 30-day retention, $0.50 runaway, p95 ≤ 2 h. The
Vision is specific to this product (13 employees, $2, a PC that must be on).

### Findings
- **low** NFR-4 repeats FR-20 ("encryption everywhere"). *Fix:* keep the pointer only.

## Strategic coherence: strong

The features follow the thesis: intake → identical processing → commit rules → guardrails →
reversibility. SM-1 to SM-3 validate it directly, and the counter-metrics block the obvious ways
to game it.

### Findings
- **low** FR-31 (portfolio pack) has no metric. *Fix:* acceptable, because it is a deliverable
  rather than an outcome. Optionally add "pack exists and passes the privacy gate".

## Done-ness clarity: adequate

Most FRs have verifiable consequences. The exceptions:

### Findings
- **high** FR-11 "Get results" has no consequence. *Fix:* "Any Day Sheet can be found by employee
  and date and downloaded in ≤ 1 min, without the AWS console."
- **medium** FR-15 "Folder Status" has no consequence. *Fix:* "Status is visible in the same place
  the Operator uploads, and reflects a change within 5 min."
- **medium** FR-3 offers two behaviours ("is refused, or makes it a new Pending upload"). *Fix:*
  choose one. A Ready Folder can't be changed; to correct a day, the Operator uploads the complete
  Folder again, which re-processes it as FR-7 does (matches today's re-run behaviour).
- **medium** The Glossary lists `Failed` as a Folder Status, but FR-9 says a failed Run "returns to
  Pending". *Fix:* define `Failed` as "the last Run failed and a retry is scheduled", or drop it.

## Scope honesty: strong

The Non-goals do real work (no OCR or rule changes, no Web App change, no services added for the
CV, developer tools stay on the PC). Accepted deviations are explicit. Open items are proportionate
to the stakes: 5 open questions, none of which blocks the PRD.

## Downstream usability: adequate

The Glossary is present and IDs are contiguous (FR-1 to FR-31, SM-1 to SM-6, SM-C1 to SM-C3).

### Findings
- **medium** "System" is used as a defined actor (FR-2, FR-4, FR-8, FR-17) but isn't in the
  Glossary. *Fix:* add "System: the AWS deployment of calls-log".
- **low** "person-day" (FR-6, FR-7, FR-30) isn't defined; the Glossary has "Person-Day Folder".
  *Fix:* add "Person-Day: one employee on one date".
- **low** "Status" and "Folder Status" are used interchangeably. *Fix:* use "Folder Status" in FRs,
  or define "Status" as short for it.

## Shape fit: strong

A capability spec with three light UJs suits a single-operator internal tool. The brownfield
references are accurate (`resync_output.py`, `sprs.xlsx`, `secrets_local.py`, per-day exception
limit).

## Mechanical notes
- OQ IDs skip OQ-3 and OQ-7 after resolution. That's intentional and noted under §12.
- No inline `[ASSUMPTION]` tags remain; §13 records every confirmed decision with its location.
- The UJs use roles (Operator, Owner), not named people. That's acceptable for a single-operator
  tool.
