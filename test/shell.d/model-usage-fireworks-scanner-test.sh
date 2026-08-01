#!/bin/bash

source "$(dirname "$0")/base-test.sh"

require_command jq
require_command python3

TEST_HOME=$(mktemp -d)
trap 'rm -rf "$TEST_HOME"' EXIT

auth_file="$TEST_HOME/auth.ini"
cat >"$auth_file" <<'EOF'
[default]
api_key = fw_test
account_id = example
EOF

result=$(python3 - "$ROOT/shell/plugins/model-usage/scripts/fireworks_usage_scanner.py" "$auth_file" <<'PY'
import importlib.util
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

scanner_path = Path(sys.argv[1])
auth_path = Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("fireworks_usage_scanner", scanner_path)
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)

payload = {
  "serverlessCosts": [
    {
      "startTime": "2026-07-31T00:00:00Z",
      "promptTokens": "100",
      "cachedPromptTokens": "40",
      "uncachedPromptTokens": "60",
      "completionTokens": "20",
      "group": {"model_name": "accounts/fireworks/models/kimi-k2p5"},
    },
    {
      "startTime": "2026-07-30T00:00:00Z",
      "promptTokens": "300",
      "cachedPromptTokens": "0",
      "completionTokens": "50",
      "group": {"model_name": "accounts/fireworks/models/deepseek-v3p2"},
    },
    {
      "startTime": "2026-07-20T00:00:00Z",
      "promptTokens": "500",
      "cachedPromptTokens": "0",
      "completionTokens": "100",
      "group": {"model_name": "accounts/fireworks/models/kimi-k2p5"},
    },
  ]
}

summary = scanner.summarize_usage(payload, date(2026, 7, 31))
api_key, account_id = scanner.read_auth_file(auth_path)
summary["apiKey"] = api_key
summary["accountId"] = account_id
summary["money"] = float(scanner.money_value({"units": "12", "nanos": 430000000}))

class BalanceFailureClient:
  def __init__(self, api_key, base_url):
    pass

  def usage(self, account_id, start_day, end_day):
    return payload

  def spent(self, account_id, start_at, end_at):
    raise scanner.FireworksError("Billing scope denied")

scanner.FireworksClient = BalanceFailureClient
os.environ["FIREWORKS_API_KEY"] = "fw_test"
scanned = scanner.scan(argparse.Namespace(
  account_id="example",
  funded_amount="20",
  funded_at="2026-07-01",
  auth_path=str(auth_path),
  api_base_url="https://example.invalid",
))
summary["balanceFailurePreservesTokens"] = (
  scanned["ready"] is True
  and scanned["modelUsage"]["kimi-k2.5"]["outputTokens"] == 120
  and scanned["usageStatusText"] == "Balance unavailable"
)
print(json.dumps(summary, separators=(",", ":")))
PY
)

[[ $(jq -r '.todayTotalTokens' <<<"$result") == "120" ]] ||
  fail "Fireworks scanner totals today's uncached, cached, and output tokens once" "$result"
pass "Fireworks scanner totals today's token categories once"

[[ $(jq -c '.modelUsage["kimi-k2.5"]' <<<"$result") == '{"inputTokens":560,"outputTokens":120,"cacheReadInputTokens":40,"cacheCreationInputTokens":0}' ]] ||
  fail "Fireworks scanner keeps cache separate in model totals" "$result"
pass "Fireworks scanner keeps cache separate in model totals"

[[ $(jq -r '.recentDays[-1].messageCount' <<<"$result") == "120" ]] ||
  fail "Fireworks scanner builds the seven-day token series" "$result"
pass "Fireworks scanner builds the seven-day token series"

[[ $(jq -r '.activeDays' <<<"$result") == "3" ]] ||
  fail "Fireworks scanner retains the 30-day model window" "$result"
pass "Fireworks scanner retains the 30-day model window"

[[ $(jq -r '.apiKey + ":" + .accountId' <<<"$result") == "fw_test:example" ]] ||
  fail "Fireworks scanner reads firectl credentials" "$result"
pass "Fireworks scanner reads firectl credentials"

[[ $(jq -r '.money' <<<"$result") == "12.43" ]] ||
  fail "Fireworks scanner parses Money units and nanos" "$result"
pass "Fireworks scanner parses Money units and nanos"

[[ $(jq -r '.balanceFailurePreservesTokens' <<<"$result") == "true" ]] ||
  fail "Fireworks scanner preserves tokens when balance lookup fails" "$result"
pass "Fireworks scanner preserves tokens when balance lookup fails"
