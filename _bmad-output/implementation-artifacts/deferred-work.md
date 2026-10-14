# Deferred work

- source_spec: none
  summary: Every remaining story in planning-artifacts/epics.md (1.1, 1.3–1.6, Epics 2–6), taken in order after Story 1.2
  evidence: "continue with implementation" spans 27 stories; the Owner chose to start with Story 1.2 (2026-10-14, decision D-14)
- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-stop-re-rotating-upright-photos.md`
  summary: A landscape-framed photo with an upright phone, where screen rectification fails, still goes through the orientation classifier and may still be turned wrongly
  evidence: maybe-false, would be medium (that image gives 0 calls). Not caused by Story 1.2: before it, the classifier ran on every image. To prove it, find a photo where `processing_log.json` shows `screen_crop_skipped`, a landscape frame and an upright phone, and check its call rows
