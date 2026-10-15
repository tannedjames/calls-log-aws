# ---------------------------------------------------------------------------
# Alerts (FR-16, FR-17). One topic for everything that should email the
# Owner: the budget now; Parked Folders and the kill switch in Epic 3. The
# Owner's address is subscribed out of band (tools/put_parameters.py
# --alert-email), so it never appears in the repo, the state or a PR plan.
#
# Not encrypted: Budgets can't publish to a topic under the AWS-managed
# aws/sns key, and the messages carry no personal data.
# Cost: the topic is free; the first 1,000 emails a month are free; one cost
# budget is free.
# ---------------------------------------------------------------------------

locals {
  alerts_topic = "calls-log-alerts"
  # Written out, not read from the topic, so the policy document can be built
  # at plan time (tests/stores.tftest.hcl checks it).
  alerts_topic_arn = "arn:aws:sns:${var.region}:${var.account_id}:${local.alerts_topic}"
}

resource "aws_sns_topic" "alerts" {
  name = local.alerts_topic
}

data "aws_iam_policy_document" "alerts_topic" {
  statement {
    sid       = "BudgetsPublish"
    actions   = ["sns:Publish"]
    resources = [local.alerts_topic_arn]

    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:budgets::${var.account_id}:*"]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts_topic.json
}

# The $2 cap (constraint 1a). AWS has no hard cap: budgets refresh up to three
# times a day, so these are early warnings; Epic 3 adds the guardrails that
# stop spending.
locals {
  budget_alerts = {
    for pair in setproduct(["ACTUAL", "FORECASTED"], [50, 80, 100]) :
    "${pair[0]}-${pair[1]}" => { type = pair[0], percent = pair[1] }
  }
}

resource "aws_budgets_budget" "monthly" {
  name         = "calls-log-monthly"
  budget_type  = "COST"
  limit_amount = "2.0"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = local.budget_alerts

    content {
      notification_type         = notification.value.type
      comparison_operator       = "GREATER_THAN"
      threshold                 = notification.value.percent
      threshold_type            = "PERCENTAGE"
      subscriber_sns_topic_arns = [aws_sns_topic.alerts.arn]
    }
  }

  depends_on = [aws_sns_topic_policy.alerts]
}
