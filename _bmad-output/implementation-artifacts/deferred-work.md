# Deferred work

- source_spec: none
  summary: Every remaining story in planning-artifacts/epics.md (1.1, 1.3–1.6, Epics 2–6), taken in order after Story 1.2
  evidence: "continue with implementation" spans 27 stories; the Owner chose to start with Story 1.2 (2026-10-14, decision D-14)
- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-stop-re-rotating-upright-photos.md`
  summary: A landscape-framed photo with an upright phone, where screen rectification fails, still goes through the orientation classifier and may still be turned wrongly
  evidence: maybe-false, would be medium (that image gives 0 calls). Not caused by Story 1.2: before it, the classifier ran on every image. To prove it, find a photo where `processing_log.json` shows `screen_crop_skipped`, a landscape frame and an upright phone, and check its call rows
- source_spec: `_bmad-output/implementation-artifacts/spec-1-5-linux-runner-image.md`
  summary: Pin the GitHub Actions in the workflows (`actions/checkout@v5`, `actions/setup-python@v6`) to full commit SHAs, and keep them current with Dependabot
  evidence: A moved or compromised tag would run new code in CI. This doesn't matter yet because the workflow holds no secrets or AWS access, but it will once Story 2.3 adds the OIDC deploy role; harden the actions as part of Story 2.2
