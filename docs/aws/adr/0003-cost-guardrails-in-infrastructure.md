# ADR 3: Cost guardrails live in infrastructure

**Status:** accepted. **Covers:** the $2 cap, runaway spend.

## Context
AWS has no hard spending cap. Code can't stop a crash loop it is part of, and a
budget alert arrives hours later. The cap has to hold structurally.

## Decision
Layers, each testable offline in Terraform:
1. **Concurrency:** the queue trigger runs at most 2 Folders at once
   (`MaximumConcurrency`), with reserved concurrency as a second cap.
2. **Kill switch:** three alarms (a message in the dead-letter queue; 6 crashes
   in 15 minutes; both slots busy for N quarter-hours) call a small Lambda that
   turns the runner's queue trigger off and emails. N is computed from the
   memory size so the worst case past the free tier stays under $0.50
   ($0.48 at 2 GB). Terraform ignores the trigger's on/off flag, so a deploy
   never turns it back on; that is a runbook step.
3. **The public page:** reserved concurrency 2, and two alarms (600 requests, or
   60 s of compute, in 15 minutes) make the kill switch set it to 0. The second
   catches a slow passphrase guesser, since each wrong guess costs a full PBKDF2.
4. **Attempts:** a Folder parks after 3 Runs instead of retrying forever.
5. **Budget** emails at 50, 80 and 100% of $2, actual and forecast; the
   account's own spend limit is the last resort.

## Consequences
- The worst case is bounded and stated, not hoped for.
- Turning things back on is deliberate and documented, never automatic.
