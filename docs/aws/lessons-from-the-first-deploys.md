# Lessons from the first deploys

Each of these passed every offline test and was found either by review before
the first apply or by the first apply itself. Each fix became a reviewed
change with a test that now pins it.

| What happened | Why | Fix |
|---|---|---|
| (Review, before any apply) Every Run would have failed reading its first checkpoint | S3 answers a missing key with 404 only to a caller allowed to list the bucket; the runner's list right carried a prefix condition, and a GET has no prefix, so the answer would have been 403 | The runner lists the bucket without a condition (key names only); a Terraform test fails if the condition returns |
| (Review) The first apply would have been refused on tagging | The provider now sends default tags to queue triggers, and the deploy role could tag functions only | The deploy role may tag queue triggers, nothing more |
| The bootstrap read the wrong AWS account from a cloud shell | The browser was signed in to another account | Terraform now refuses any account but the project's, and the shell procedure is a script that checks the account first |
| Every pull-request plan failed once the status table existed | Even an unrefreshed plan describes a DynamoDB table already in the state | The read-only plan role may describe `calls-log-*` tables (metadata only) |
| Every function creation was refused: "Lambda does not have permission to access the ECR image" | The guide says the execution role's pull right is enough in the same account; in practice CreateFunction wants the repository policy to let Lambda pull, or the caller's right to write that policy | A Terraform-managed repository policy (AWS's documented minimum), every function depending on it, and the deploy role's right to manage it. The next apply created all five functions, the queue trigger and the alarms (13 added), and the drift check found AWS matching the code: Lambda left the policy as written |
| The deploy that failed left queues, roles and log groups behind | Terraform applies what it can and records it | Nothing to undo: state recorded them, and the next apply continues |
