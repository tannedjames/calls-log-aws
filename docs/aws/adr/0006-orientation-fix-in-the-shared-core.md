# ADR 6: The orientation fix goes in the shared core

**Status:** accepted (2026-10-14). **Covers:** identical results on the PC and AWS.

## Context
Parity was checked on real reference folders before any migration. One folder
held a phone photo that the OCR library's page-orientation classifier rotated
90° the wrong way, so it read no calls at all. Upright portrait screenshots
never need that classifier.

## Decision
Run orientation classification only on landscape images. The fix goes in the
shared core, so the PC and AWS both get it: AWS never diverges from the PC
because one side was patched. The counting rules themselves were kept as the
Owner runs them, even where a hand-corrected sheet differed.

## Consequences
- All reference folders match; the fixed photo now gives its calls.
- OCR is faster as a side effect (one model less per image).
- Any later change to the core, the lockfile or a model needs the parity check
  to pass again before both systems are trusted to write.
