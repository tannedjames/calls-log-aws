---
title: 'Stories 3.1 and 3.7: the Lambda platform, the benchmark, the queue and the cost guardrails'
type: 'feature'
created: '2026-10-15'
status: 'built, waiting on the bootstrap apply and the live checks'
route: 'dispatch'
review_loop_iteration: 1
baseline_commit: '3a4b9be'
context: ['{project-root}/_bmad-output/planning-artifacts/architecture/architecture-calls-log-2026-10-14/ARCHITECTURE-SPINE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The runner (Stories 3.5, 3.6) exists only as code. Nothing in AWS runs it, nothing caps it, and the $2 plan still rests on a laptop estimate of the cost per image.

**Approach:** In `infra/`, add the runner Lambda from the ECR image, its queue and dead-letter queue, the queue trigger, the kill switch with three alarms, and switchable benchmark functions. In the bootstrap, add a permissions boundary that every function role must carry. Push the image before the apply, so the functions can point at it. Add PC tools that send Folders (`tools/enqueue.py`) and measure the benchmark (`tools/bench_lambda.py`), and a runbook. Story 3.1's measurement runs once the functions exist. It ships here because the benchmark needs the same image, role fence and deploy order.

**Decisions** (the Owner's standing instruction, D-28: work uninterrupted; recorded here):
- **Functions** (AD-11, AD-12, AD-15):
  - `calls-log-runner`: x86_64, 900 s, `runner_memory_mb` (2,048 until Story 3.1 measures), 1,024 MB of `/tmp`, no VPC, reserved concurrency 3. The account's limit is 400 (Story 2.1).
  - `calls-log-killswitch`: 512 MB, 60 s, reserved 1.
  - `calls-log-bench-{1536,2048,3008}`, only while `enable_bench` is on.
  - All run the same image. Each function's command names its handler (`/lambda-entrypoint.sh cloud.<module>.handler`), and the image keeps its entrypoint, which links the baked models.
- **Queue:** `calls-log-runs` (visibility 1,200 s, 14 days, SSE-SQS) with `calls-log-runs-dlq` after 10 receives, as a crash backstop; attempts park a Folder after 3 Runs (AD-6). The trigger: batch 1, `ReportBatchItemFailures`, `MaximumConcurrency` 2. Its `enabled` is ignored by Terraform, so a deploy never undoes the kill switch.
- **Guardrails:** three alarms, each calling the kill switch and emailing through `calls-log-alerts`:
  - the dead-letter queue holds a message;
  - 6 or more runner errors in 15 minutes;
  - both Run slots busy for N quarter-hours in a row, the "GB-s over 15 min" proxy. N is computed from the memory size so that the worst case, counting one more period for the stop, stays at or under $0.50: 7 at 2,048 MB ($0.48), 4 at 3,008 MB.

  The kill switch turns the runner's queue trigger off and emails; turning it back on is a runbook step.
- **Role fence (bootstrap):**
  - `calls-log-workload-boundary` lists the most any function role may do: this project's bucket, table, parameters, queues, topic, image and logs, plus turning a `calls-log-*` function's queue trigger off.
  - The deploy role may create or change roles only under `/calls-log/` with that boundary, and pass them only to Lambda.
  - The deploy role may manage `calls-log-*` functions, triggers, queues, log groups and alarms.
- **Deploy order:** build, test in the image, push `:<sha>`, then `apply -var runner_image_tag=<sha>`, then the drift check. A new account gets the registry first, through a targeted apply. PR plans use the PR head's SHA.
- **PC tools** use the AWS CLI through `tools/aws_cli.py` (no boto3 on the PC). Its client has boto3's call shape, so `tools/enqueue.py` writes Folder Status through `cloud/status.py`, still the table's only writer (AD-4).
- **Benchmark:** `cloud/bench.py` runs a `bench/<n>/` prefix through `cloud.runner.extract` and returns timings only. `tools/bench_lambda.py` uploads Synthetic Set folders, invokes each size cold then warm, and reads Lambda's REPORT line. Then it projects 12,000 images and 450 Runs a month (every Run cold) against 350,000 GB-s, and runs the 40-image folder at the cheapest size.

## Boundaries & Constraints

**Always:** `ap-southeast-2`; every function role under `/calls-log/` with the boundary; the bootstrap applied by hand before the PR that needs it merges; only made-up images in `bench/`.

**Never:** a VPC, NAT or endpoint; a function role without the boundary; the deploy role editing its own role, the plan role or the boundary; a deploy that re-enables a stopped trigger; employee screenshots in the benchmark.

</frozen-after-approval>

## Code Map

- `infra/runner.tf`, `guardrails.tf`, `bench.tf`, `variables.tf` (`runner_image_tag`, `runner_memory_mb`, `enable_bench`), `outputs.tf`, `alerts.tf` (CloudWatch may publish), `data.tf` (`bench/` expires).
- `infra/bootstrap/main.tf`: `workload_boundary`, `deploy_lambda_platform`; `tests/ci_roles.tftest.hcl`.
- `infra/tests/runner.tftest.hcl` and the `runner_image_tag` variable in the other two test files.
- `.github/workflows/deploy.yml`: the order above. `ci.yml`: `infra/` is mounted read-only for the unit tests.
- `cloud/killswitch.py`, `cloud/bench.py`, `cloud/settings.py` (`runner_function`, `from_environment`).
- `tools/aws_cli.py`, `tools/enqueue.py`, `tools/bench_lambda.py`.
- `docs/TECHNICAL.md` §1, §6, §10, §11 (Runbook); `docs/AWS_SETUP.md` §3, §5; the `Dockerfile` comment.

## Tasks & Acceptance

**Execution:**
- [x] Bootstrap boundary and fenced deploy rights; 9 offline tests (5 mutations caught, WIP commit `1adf9af`).
- [x] `infra/` runner, queue, DLQ, trigger, kill switch, alarms, bench; 12 offline tests.
- [x] `deploy.yml`: push before apply, the image tag, the registry first on a new account.
- [x] `cloud/killswitch.py`, `cloud/bench.py`, `tools/aws_cli.py`, `tools/enqueue.py`, `tools/bench_lambda.py`, with 21 tests.
- [x] Runbook and setup docs.
- [ ] **Owner:** bootstrap plan, then approve, then apply on the PC (adds the boundary and the `lambda-platform` policy, plus the 5 from #9 and #13 if not yet applied).
- [ ] **Owner:** merge in order #9, #10, #11, #12, #13, #14, then this branch; the first deploy creates the functions.
- [ ] **Owner / next session:** `put_parameters.py --alert-email` and confirm; Story 3.1 benchmark (`docs/AWS_SETUP.md` §5) and its PR (`runner_memory_mb`, `enable_bench = false`).
- [ ] **Live matrix** (Synthetic Set only):
  - one Folder through `enqueue.py` reaches Done with `results/` written (sync off);
  - a poison Folder Parks after 3 Runs with one email;
  - 50 Folders never show more than 2 concurrent Runs;
  - a forced alarm (`aws cloudwatch set-alarm-state`) stops the trigger within 5 minutes and emails; the runbook turns it back on.

**Acceptance Criteria:** epics.md Stories 3.1 and 3.7. Offline, the Terraform tests pin the configuration (concurrency, timeouts, no VPC, the role fence, the alarm actions, the $0.50 bound, and the bench removed when off), and `tests.py` pins the code. The rest needs AWS: the matrix above.

## Implementation Notes

- 2026-10-15, WIP `1adf9af` (stacked on #13): bootstrap, `infra/` and the Terraform tests.
- 2026-10-16, finished on `claude/nice-ritchie-qunmoe`:
  - merged the runner chain (#10, #11, #12, #14) into the WIP, because the functions run `cloud.runner.handler` and `enqueue.py` writes through `cloud.status`. `tests.py` conflicted only by both sides appending; kept both.
  - **Verified here** (Terraform 1.16.5, provider 6.69.0 from releases.hashicorp.com through a local mirror, because the registry is blocked from this container): `fmt -check` clean, `validate` clean for both modules, `terraform test` infra 12 passed and bootstrap 9 passed. `run_tests.py`: 252 passed (21 new) in a Linux venv with numpy, Pillow, openpyxl and OpenCV. The image wasn't built here: Docker pulls from public.ecr.aws are blocked by the proxy, so CI is the first in-image run.
  - **Mutations** (each reverted after its run):
    - `mark_ready` moved before the uploads: 3 tests fail;
    - the kill switch updates a trigger that's already off: 2 fail;
    - a non-ASCII CLI input file: 2 fail;
    - no time cut-off in the bench: 1 fails;
    - an environment name `settings.py` doesn't read: 1 fails;
    - the runner's `ListBucket` given back its `s3:prefix` condition: the Terraform test fails.
- **AWS documentation checked:**
  - **Image pull:** for a same-account image, the execution role or the repository policy may grant the pull; only one is needed (Lambda guide, images-create). The roles keep their `Image` statement, and no repository policy is added.
  - **Secrets:** `aws/ssm`'s key policy grants `Principal: "*"` within the account, and the implicit deny of a boundary doesn't limit that. The runner can decrypt `/calls-log/` parameters without KMS in the boundary.
  - **Concurrency metric:** `ConcurrentExecutions` is emitted per function, so the runaway alarm has data.
  - **Queue trigger condition:** `lambda:FunctionArn` restricts event-source-mapping actions by their function, so the kill switch's `UpdateEventSourceMapping` by UUID is allowed.

## Review Triage Log

Pass 1, 2026-10-16, inline (adversarial, edge cases, verification gaps) over the WIP plus this session's diff.

- **high (patched):** the runner role listed the bucket only under the `s3:prefix` condition `inbox/*`, `work/*`. S3 returns 404 for a missing key only to a caller allowed to `ListBucket`, otherwise 403, and a `GetObject` request carries no `s3:prefix`. So every image's first checkpoint read (`work/<id>/<file>.json`, absent by design) would likely raise AccessDenied, and every Run would fail. The unit tests' fake S3 raised `NoSuchKey`, so nothing caught it. `ListBucket` now covers the bucket with no condition (key names only), pinned by a Terraform assertion that fails with the condition back.
- **high (patched):** `default_tags` also tag the event source mapping (provider 6.69 gives it `tags`). Creating it sends tags, and every refresh calls `lambda:ListTags` on `event-source-mapping:<uuid>`. The deploy role had tag rights on functions only, so the first apply would have failed. Added `TagQueueTriggers` (tag, untag, list tags on `event-source-mapping:*`), which can't change what a trigger does.
- **medium (patched):** the deploy applied before pushing. With functions in `infra/`, the apply would point Lambda at `:<sha>` before that tag existed, and a new account had no registry to push to. Reordered: push, then apply, with the registry first via a targeted apply when the state lacks it. A failed build now applies nothing.
- **medium (patched):** two new tests read `infra/*.tf`, which isn't in the image, and CI runs the unit tests only in the image. They return early without `infra/`, and CI mounts it read-only, so they run in CI and on the PC. The deploy job's in-image run follows a green CI for the same commit.
- **low (patched):** `bench/` lives in the versioned bucket with no lifecycle rule, so its overwritten versions would stay forever. Added to `expiring_prefixes`; the store tests are updated.
- **low (rejected):** `enqueue.py` doesn't check the name against `/calls-log/name_prefixes`. The PC doesn't refuse unknown names either (the Web App refuses them at sync), so a check here would make AWS stricter than the PC. Story 4.2's page validates.
- **low (deferred to the live matrix):** whether `CreateFunction` from the deploy role also needs `ecr:SetRepositoryPolicy` (Lambda auto-adding a repository policy). Per the current docs the execution role suffices. If the first apply fails there, add an `aws_ecr_repository_policy` for `lambda.amazonaws.com` scoped to `function:calls-log-*` and the matching bootstrap right.

## Verification

**Commands:**
- `terraform fmt -check -recursive infra`; `init -backend=false`, `validate` and `test` in `infra/` and `infra/bootstrap/`.
- `python -X utf8 run_tests.py`: 252 passed.
- `python -X utf8 tools/check_private_data.py`: OK (fail-closed runs in CI with the denylist).
