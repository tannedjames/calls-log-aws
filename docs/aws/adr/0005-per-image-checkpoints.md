# ADR 5: Per-image checkpoints

**Status:** accepted. **Covers:** 40-image folders inside a 15-minute Lambda.

## Context
A laptop took about 11 s per image; 40 images could approach Lambda's
15-minute limit, and a Folder that always times out would fail forever.

## Decision
After each image, the runner writes that image's parsed call records to
`work/<upload>/<image>.json`. A resumed Run loads the checkpoints, OCRs only the
missing images, and merges everything in the original sorted order before
de-duplication, so the sessions are identical to a single uninterrupted Run.
Checkpoints expire with the uploads after 30 days.

## Consequences
- A long Folder finishes over two Runs instead of failing.
- The order guarantee is tested: a resumed Run gives the same sessions.
- The benchmark (Story 3.1) also measures whether a 40-image Folder fits in one Run.
