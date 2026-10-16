# ADR 4: One Run per person and day (a lease)

**Status:** accepted. **Covers:** never two Runs for the same day; no duplicate sheet tabs.

## Context
SQS delivers at least once, and the same day can be uploaded again while it is
still running. Two Runs for one person and day would race to write the same
spreadsheet tab. Folder names also arrive in different Unicode forms
(Vietnamese accents typed on different devices).

## Decision
Before any work, the runner takes a conditional lease in DynamoDB on the
person-day key (NFC-normalised), expiring after 1,000 s: longer than the 900 s
Lambda limit, shorter than the queue's 1,200 s visibility. A Run that misses the
lease returns its message unprocessed, and that doesn't count as an attempt.
Holding the lease, it counts the attempt before processing, so a timeout is
counted too; the fourth start parks the Folder.

## Consequences
- A re-upload during a Run simply waits its turn, as the PC's re-runs did.
- Every state change is a conditional write from the expected prior state, by
  one module only, so a refused change leaves the record untouched.
