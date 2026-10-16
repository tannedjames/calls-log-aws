---
title: 'Story 6.1: architecture and decisions write-up'
type: 'docs'
created: '2026-10-16'
status: 'built; Story 6.2 waits for the first full month after cutover'
route: 'dispatch'
---

## Intent

A sanitised pack in `docs/aws/` the Owner can show and defend: the Route B
diagram, decision records for the key choices, the runbook, and what the first
live deploys taught. No employee names, IDs, URLs, secrets, phone numbers,
account numbers or token subjects.

## Contents

- `docs/aws/README.md` (index), `architecture.md` (diagram, a Folder's path,
  the fences, the cost model, delivery).
- ADRs 1-6 as the AC lists (route, no VPC, guardrails, lease, checkpoints,
  orientation fix), plus 7 (hand-applied bootstrap and role fence) and 8 (secrets
  out of state).
- `lessons-from-the-first-deploys.md`; the ECR row records the verified fix
  (#18's deploy: 13 added, no drift).
- The runbook stays in `docs/TECHNICAL.md` §11 and the Operator guide in
  `docs/OPERATOR_GUIDE.md`; the index links both rather than copying them.

## Verification

- `tools/check_private_data.py`: OK; a grep for the account ID, the GitHub
  owner and its immutable IDs, an email and a Web App URL finds nothing in
  `docs/aws/`.
- Story 6.2 (measured cost and turnaround) needs a month of real use; its
  inputs are `tools/turnaround.py`, the comparison log, the benchmark report and
  the bill.
