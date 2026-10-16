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
- source_spec: `_bmad-output/implementation-artifacts/spec-3-1-and-3-7-lambda-platform.md`
  summary: If the first apply of the functions fails at CreateFunction with an ECR permission error, add an `aws_ecr_repository_policy` granting `lambda.amazonaws.com` `ecr:BatchGetImage` and `ecr:GetDownloadUrlForLayer` (condition `aws:SourceArn` = `function:calls-log-*`), plus `ecr:SetRepositoryPolicy`, `GetRepositoryPolicy` and `DeleteRepositoryPolicy` for the deploy role in the bootstrap
  evidence: unverified, would be high (no function can be created). The Lambda guide says the execution role or the repository policy is enough, and the roles have the pull rights; only the first live apply settles it
  resolved: 2026-10-16. #16's first apply failed exactly so ("Lambda does not have permission to access the ECR image"), after creating the queues, roles and log groups. Fixed on `claude/fix-ecr-pull` with AWS's documented minimum statement (no condition: a conditioned one risks Lambda judging it insufficient and rewriting the policy, which the drift check would then flag; cross-account use still needs an account grant that isn't there), every function depending on it, and the three repository-policy rights for the deploy role (plus `GetRepositoryPolicy` for the plan role)
