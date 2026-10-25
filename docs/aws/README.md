# calls-log on AWS: the case-study pack

calls-log reads employees' phone call-log screenshots with OCR, turns each day
into working sessions, and writes them to a spreadsheet. It ran on one
Windows PC. This pack is how it moved to AWS for under $2 a month, with no
functional change, no long-lived keys and a way back to the PC at any time.

Nothing here holds employee data: no names, IDs, URLs, secrets or phone
numbers. The CI privacy gate checks every file (Story 2.2).

| | |
|---|---|
| [Architecture](architecture.md) | Route B: an upload page, a queue and a Lambda runner around the unchanged pipeline |
| Decision records | [1 Route B](adr/0001-route-b-serverless-queue.md) · [2 No VPC](adr/0002-no-vpc.md) · [3 Guardrails](adr/0003-cost-guardrails-in-infrastructure.md) · [4 Single-flight lease](adr/0004-single-flight-lease.md) · [5 Checkpoints](adr/0005-per-image-checkpoints.md) · [6 Orientation fix](adr/0006-orientation-fix-in-the-shared-core.md) · [7 Hand-applied bootstrap](adr/0007-hand-applied-bootstrap-and-role-fence.md) · [8 Secrets out of state](adr/0008-secrets-out-of-terraform-state.md) · [9 Data lake](adr/0009-call-rows-in-an-s3-data-lake.md) (proposed) |
| [Data contract](data-contract.md) · [Data lake plan](data-lake-plan.md) | The call, session and upload rows the lake keeps, with no names; the phases from staged records to a public demo |
| [Lessons from the first deploys](lessons-from-the-first-deploys.md) | What the documentation said, what AWS did, and the fix for each |
| Runbook | `docs/TECHNICAL.md` §11: sending Folders, the upload page, the kill switch, re-queues and re-sends, the switch-over and the way back |
| [Operator guide](../OPERATOR_GUIDE.md) | The one page the person who uploads needs |
| [Comparison log](comparison-log.md) | Two weeks of PC against AWS, by F-label (Story 5.2) |
| Measured cost and turnaround | After the first full month (Story 6.2): the bill by service, GB-s against the free 400,000, p95 Ready-to-Sync, against the planning estimates ($0.39 a month, about 11 s per image on a laptop) |
| Synthetic Set | `tools/gen_synth.py`: made-up screenshots with known answers, the only images CI and the benchmark ever see |
