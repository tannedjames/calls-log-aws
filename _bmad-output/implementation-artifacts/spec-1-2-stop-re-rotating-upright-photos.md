---
title: 'Story 1.2: Stop re-rotating upright photos'
type: 'bugfix'
created: '2026-10-14'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: ['{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** PaddleOCR's page-orientation classifier runs on every image. It turned an upright phone photo 90°, so that image yielded 0 calls and Reference Set folder 3 lost 7 calls (08:33–08:47).

**Approach:** Ask PaddleOCR to classify page orientation only when the image it receives (after preprocessing) is landscape, meaning wider than tall. Portrait images go through as they are. The engine is still built with the classifier loaded, so landscape photos keep being turned upright. The change is in `ocr.py`, which the PC and AWS share, plus a stubbed unit test and the docs.

**Decisions:**
- "Landscape" means `width > height` on the preprocessed image. A square image counts as portrait.
- `paddle_use_doc_orientation = False` still turns the classifier off for every image.
- Accepted consequence (from FR32): an upside-down portrait photo (180°) is no longer flipped automatically. Screenshots are never upside down; such a photo has to be retaken. The docs say so.
- Done when: the stubbed test proves the portrait/landscape decision; all three Reference Set folders match their expected Sessions (folder 3 = the 6 rule-based Sessions, folders 1 and 2 unchanged at 101 and 102 call rows) in a read-only run with no sync and nothing written to the folders; a synthetic screenshot rotated 90° is still read; all existing tests pass.

</frozen-after-approval>

## Implementation Notes

- PaddleOCR 3.x `predict()` accepts `use_doc_orientation_classify` per call (checked on 3.7.0), so the engine is still built with the classifier loaded and each image decides. No second engine, no change to `run_ocr()`.
- `ocr.py`: new `_wants_doc_orientation(img)` (`CONFIG.paddle_use_doc_orientation and w > h`); `_ocr_paddle` passes the result to `_paddle_predict(arr, classify)`, including on the oneDNN rebuild path. The 2.x API path ignores it, since 2.x has no page classifier.
- The decision uses the image `run_ocr()` receives, which is the preprocessed image. EXIF orientation is applied during loading, so phone-photo metadata can't swap width and height.
- Comments updated in `config.py` and in the `image_preprocess.py` no-OSD message. Docs: `docs/TECHNICAL.md` §2, §4 *Rotation*, §6 and §8; `README.md` photo tip ("the right way up").
- `tests.py`: `test_doc_orientation_only_for_landscape` (portrait, square, landscape) and `test_doc_orientation_setting_off_never_classifies`, using a stub engine. On the old code they fail with a `KeyError`, because `predict()` got no flag.
- No git in this folder, so the review diff came from a snapshot taken before editing (scratchpad). Review ran inline, at the Owner's choice.
- Verification (2026-10-14):
  - `run_tests.py`: 85 passed, 0 failed (81 existing + 4 new).
  - Read-only Reference Set run (no sync, nothing written into the folders, labels F1–F3): identical to the expected results on every count. F1: 3 Sessions, 101 records. F2: 5 Sessions, 102 records. F3: the 6 rule-based Sessions, 110 records (104 countable), the 7 photo calls recovered.
  - Synthetic shot and photo turned 90° and 270°: same 11 calls as upright, so landscape images are still classified.
  - The wall time (273/464/702 s) was 2–4× slower than the earlier run because other heavy processes were running at the same time. It is not a measurement of the change.
- `tools/check_private_data.py` couldn't run: it needs git, and this folder isn't a repository. Every changed file was written in this session and holds no employee data.
- `graphify update .` was run after the code change.

## Review Triage Log

- low (patched): `README.md` photo tips didn't say "the right way up", and an upside-down photo now gives no calls. Added to tip 5.
- maybe-false (deferred): a landscape-framed photo with an upright phone, where rectification fails, still goes through the classifier. This isn't caused by this change (before it, the classifier ran on every image). It would be medium if true; see `deferred-work.md`.
- false: an older PaddleOCR 3.x on the office PC might lack the per-call flag. The office PC moves to the pinned 3.7.0 through Story 1.1 (D-14). A missing keyword would raise a `TypeError`, which `ocr_strict_engine` makes fatal, so it can't go wrong silently.
- low (rejected): `classify` is unused on the PaddleOCR 2.x path. 2.x has no page classifier, so there is nothing to pass it to.
- low (rejected): no test covers the oneDNN rebuild path passing `classify`. That path needs `paddle_enable_mkldnn = True`, which the config invariant forbids.
