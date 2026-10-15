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
- source_spec: `_bmad-output/implementation-artifacts/spec-2-3-deploy-pipeline-oidc-ecr-terraform.md`
  summary: For Story 2.4, keep the real SSM secret values out of Terraform state. Refreshing an `aws_ssm_parameter` stores the real value in state even with `ignore_changes = [value]`. The PR plan role reads the state and must never get `ssm:GetParameter`
  evidence: unverified, would be high (the Web App secret would reach the state file, and anyone who can open a PR could read it with the plan role). To settle it: check provider 6.69's `aws_ssm_parameter` for a write-only `value_wo`, or keep the parameter values outside Terraform entirely, and confirm with `terraform state pull` after the 2.4 apply that no real value is present
- source_spec: `_bmad-output/implementation-artifacts/spec-2-3-deploy-pipeline-oidc-ecr-terraform.md`
  summary: Keep the runner image's OS packages patched. The first ECR basic scan found 0 critical, 15 high, 20 medium and 5 low findings, all in Amazon Linux packages of the digest-pinned Lambda base (curl, libX11, openssl FIPS/snapsafe libs, pcre2, rpm). Add Dependabot's `docker` ecosystem to bump the base digest, and/or `dnf upgrade`, before Epic 3 runs real Folders
  evidence: `aws ecr describe-image-scan-findings --repository-name calls-log-runner --image-id imageTag=c008824ec4d82b442360ee0abd8494cdd2a7f917`, 2026-10-15. The runner has no inbound network path (no VPC, SQS-triggered), which lowers the exposure, but a stale base image accumulates findings
