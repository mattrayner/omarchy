from __future__ import annotations

import argparse
import configparser
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


API_BASE_URL = "https://api.fireworks.ai"


class FireworksError(Exception):
  pass


def number(value: Any) -> int:
  try:
    return max(0, round(float(value or 0)))
  except (TypeError, ValueError):
    return 0


def money_value(value: Any) -> Decimal:
  if not isinstance(value, dict):
    return Decimal("0")
  try:
    units = Decimal(str(value.get("units", 0) or 0))
    nanos = Decimal(str(value.get("nanos", 0) or 0)) / Decimal("1000000000")
    return units + nanos
  except (InvalidOperation, TypeError, ValueError):
    return Decimal("0")


def model_id(row: dict[str, Any]) -> str:
  group = row.get("group") if isinstance(row.get("group"), dict) else {}
  raw = group.get("model_name") or row.get("modelName") or "unknown"
  name = str(raw).rstrip("/").split("/")[-1] or "unknown"
  return re.sub(r"(?<=\d)p(?=\d)", ".", name)


def row_date(row: dict[str, Any]) -> str:
  raw = str(row.get("startTime") or "")
  return raw[:10] if len(raw) >= 10 else ""


def empty_bucket() -> dict[str, int]:
  return {
    "inputTokens": 0,
    "outputTokens": 0,
    "cacheReadInputTokens": 0,
    "cacheCreationInputTokens": 0,
  }


def summarize_usage(payload: dict[str, Any], today: date | None = None) -> dict[str, Any]:
  today = today or datetime.now().astimezone().date()
  recent_dates = [(today - timedelta(days=offset)).isoformat() for offset in range(6, -1, -1)]
  recent = {day: 0 for day in recent_dates}
  today_by_model: dict[str, int] = {}
  model_usage: dict[str, dict[str, int]] = {}
  active_dates: set[str] = set()

  rows = payload.get("serverlessCosts")
  if not isinstance(rows, list):
    rows = []

  for raw_row in rows:
    if not isinstance(raw_row, dict):
      continue
    day = row_date(raw_row)
    model = model_id(raw_row)
    prompt = number(raw_row.get("promptTokens"))
    cached = min(prompt, number(raw_row.get("cachedPromptTokens")))
    uncached = number(raw_row.get("uncachedPromptTokens"))
    if "uncachedPromptTokens" not in raw_row:
      uncached = max(0, prompt - cached)
    output = number(raw_row.get("completionTokens"))
    total = uncached + cached + output
    if total <= 0:
      continue

    bucket = model_usage.setdefault(model, empty_bucket())
    bucket["inputTokens"] += uncached
    bucket["outputTokens"] += output
    bucket["cacheReadInputTokens"] += cached

    if day:
      active_dates.add(day)
    if day in recent:
      recent[day] += total
    if day == today.isoformat():
      today_by_model[model] = today_by_model.get(model, 0) + total

  recent_days = [{"date": day, "messageCount": recent[day]} for day in recent_dates]
  today_total = sum(today_by_model.values())
  return {
    "todayPrompts": 0,
    "todaySessions": 0,
    "todayTotalTokens": today_total,
    "todayTokensByModel": today_by_model,
    "recentDays": recent_days,
    "totalPrompts": 0,
    "totalSessions": 0,
    "activeDays": len(active_dates),
    "activeDates": sorted(active_dates),
    "modelUsage": model_usage,
  }


def read_auth_file(path: Path) -> tuple[str, str]:
  if not path.is_file():
    return "", ""

  parser = configparser.ConfigParser(interpolation=None)
  try:
    parser.read(path)
  except configparser.Error:
    return "", ""

  api_key = ""
  account_id = ""
  sections = [parser.defaults()]
  sections.extend(parser[section] for section in parser.sections())
  for values in sections:
    api_key = api_key or str(values.get("api_key", values.get("api-key", ""))).strip()
    account_id = account_id or str(values.get("account_id", values.get("account-id", ""))).strip()
  return api_key, account_id


def credentials(auth_path: Path, requested_account_id: str) -> tuple[str, str]:
  file_key, file_account = read_auth_file(auth_path)
  api_key = str(os.environ.get("FIREWORKS_API_KEY", "")).strip() or file_key
  account_id = (
    str(requested_account_id or "").strip()
    or str(os.environ.get("FIREWORKS_ACCOUNT_ID", "")).strip()
    or file_account
  )
  return api_key, account_id


def normalize_account_id(value: str) -> str:
  return str(value or "").strip().removeprefix("accounts/").strip("/")


def timezone_name() -> str:
  configured = str(os.environ.get("TZ", "")).strip()
  if configured:
    return configured
  try:
    target = (Path("/etc/localtime").resolve()).as_posix()
    marker = "/zoneinfo/"
    if marker in target:
      return target.split(marker, 1)[1]
  except OSError:
    pass
  return "UTC"


def iso_timestamp(value: str) -> str:
  raw = str(value or "").strip()
  if not raw:
    return ""
  try:
    if len(raw) == 10:
      parsed = datetime.combine(date.fromisoformat(raw), time.min, tzinfo=timezone.utc)
    else:
      parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
      if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
  except ValueError:
    raise FireworksError("Fireworks fundedAt must be an ISO date such as 2026-07-01")


class FireworksClient:
  def __init__(self, api_key: str, base_url: str = API_BASE_URL):
    self.api_key = api_key
    self.base_url = base_url.rstrip("/")

  def request(
    self,
    path: str,
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
  ) -> dict[str, Any]:
    url = self.base_url + path
    if query:
      url += "?" + urllib.parse.urlencode(query, doseq=True)
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
      url,
      data=data,
      method="POST" if body is not None else "GET",
      headers={
        "Authorization": "Bearer " + self.api_key,
        "Accept": "application/json",
        "Content-Type": "application/json",
      },
    )
    try:
      with urllib.request.urlopen(request, timeout=15) as response:
        decoded = json.load(response)
        return decoded if isinstance(decoded, dict) else {}
    except urllib.error.HTTPError as error:
      if error.code == 401:
        raise FireworksError("Fireworks rejected the API key")
      if error.code == 403:
        raise FireworksError("The Fireworks API key cannot read billing data")
      if error.code == 404:
        raise FireworksError("Fireworks account not found")
      raise FireworksError(f"Fireworks API returned HTTP {error.code}")
    except urllib.error.URLError as error:
      raise FireworksError("Could not reach the Fireworks API") from error
    except (json.JSONDecodeError, TimeoutError) as error:
      raise FireworksError("Fireworks returned an invalid billing response") from error

  def discover_account(self) -> tuple[str, dict[str, Any]]:
    payload = self.request("/v1/accounts", query={"pageSize": 100})
    accounts = [item for item in payload.get("accounts", []) if isinstance(item, dict)]
    if len(accounts) == 1:
      account = accounts[0]
      return normalize_account_id(str(account.get("name") or "")), account
    if not accounts:
      raise FireworksError("No Fireworks account is available for this API key")
    raise FireworksError("Set a Fireworks account ID when the API key can access multiple accounts")

  def account(self, account_id: str) -> dict[str, Any]:
    quoted = urllib.parse.quote(normalize_account_id(account_id), safe="")
    return self.request(f"/v1/accounts/{quoted}")

  def usage(self, account_id: str, start_day: date, end_day: date) -> dict[str, Any]:
    quoted = urllib.parse.quote(normalize_account_id(account_id), safe="")
    query = {
      "startTime": start_day.isoformat() + "T00:00:00Z",
      "endTime": end_day.isoformat() + "T00:00:00Z",
      "usageType": "SERVERLESS",
      "timezone": timezone_name(),
      "groupBy": ["model_name"],
    }
    return self.request(f"/v1/accounts/{quoted}/billingUsage", query=query)

  def spent(self, account_id: str, start_at: str, end_at: str) -> Decimal:
    quoted = urllib.parse.quote(normalize_account_id(account_id), safe="")
    body = {
      "startTime": start_at,
      "endTime": end_at,
      "scope": "ACCOUNT",
    }
    try:
      payload = self.request(f"/v1/accounts/{quoted}/usageCosts:query", body=body)
      if not isinstance(payload.get("subtotal"), dict):
        raise FireworksError("Fireworks cost response did not include a subtotal")
      return money_value(payload.get("subtotal"))
    except FireworksError:
      parsed_end = datetime.fromisoformat(end_at.replace("Z", "+00:00"))
      summary_end = (parsed_end.date() + timedelta(days=1)).isoformat() + "T00:00:00Z"
      payload = self.request(
        f"/v1/accounts/{quoted}/billing/summary",
        query={"startTime": start_at, "endTime": summary_end},
      )
      return sum(
        (money_value(item.get("totalCost")) for item in payload.get("lineItems", []) if isinstance(item, dict)),
        Decimal("0"),
      )


def error_result(message: str) -> dict[str, Any]:
  return {
    "ready": False,
    "hasLocalStats": False,
    "usageStatusText": "Fireworks unavailable",
    "authHelpText": message,
  }


def scan(args: argparse.Namespace) -> dict[str, Any]:
  api_key, account_id = credentials(Path(args.auth_path).expanduser(), args.account_id)
  if not api_key:
    return error_result("Set FIREWORKS_API_KEY or run `firectl set-api-key`.")

  client = FireworksClient(api_key, args.api_base_url)
  account: dict[str, Any] = {}
  if account_id:
    account_id = normalize_account_id(account_id)
  else:
    account_id, account = client.discover_account()

  today = datetime.now().astimezone().date()
  usage = client.usage(account_id, today - timedelta(days=29), today + timedelta(days=1))
  result = summarize_usage(usage, today)
  result.update({
    "ready": True,
    "hasLocalStats": True,
    "hasPromptStats": False,
    "tierLabel": "Prepaid",
    "usageStatusText": "",
    "authHelpText": "",
    "accountId": account_id,
    "accountName": str(account.get("displayName") or ""),
    "balanceRemaining": -1,
    "balanceFunded": -1,
    "balanceSpent": -1,
    "balanceCurrency": "USD",
    "balanceEstimated": True,
  })

  try:
    try:
      funded = Decimal(str(args.funded_amount or "0"))
    except InvalidOperation:
      raise FireworksError("Fireworks fundedAmount must be a number")
    if not funded.is_finite():
      raise FireworksError("Fireworks fundedAmount must be a finite number")
    if funded > 0:
      funded_at = iso_timestamp(args.funded_at)
      if not funded_at:
        if not account:
          account = client.account(account_id)
        funded_at = iso_timestamp(str(account.get("createTime") or ""))
      if not funded_at:
        raise FireworksError("Set fundedAt because the Fireworks account creation date is unavailable")

      end_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
      spent = max(Decimal("0"), client.spent(account_id, funded_at, end_at))
      remaining = max(Decimal("0"), funded - spent)
      result["balanceRemaining"] = float(remaining)
      result["balanceFunded"] = float(funded)
      result["balanceSpent"] = float(spent)
  except FireworksError as error:
    result["usageStatusText"] = "Balance unavailable"
    result["authHelpText"] = str(error)

  return result


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description="Read Fireworks token usage and estimated prepaid balance")
  parser.add_argument("--account-id", default="")
  parser.add_argument("--funded-amount", default="0")
  parser.add_argument("--funded-at", default="")
  parser.add_argument("--auth-path", default="~/.fireworks/auth.ini")
  parser.add_argument("--api-base-url", default=os.environ.get("FIREWORKS_API_BASE_URL", API_BASE_URL))
  return parser.parse_args()


def main() -> int:
  try:
    print(json.dumps(scan(parse_args()), separators=(",", ":")))
  except FireworksError as error:
    print(json.dumps(error_result(str(error)), separators=(",", ":")))
  except Exception as error:
    print(json.dumps(error_result("Fireworks usage scan failed"), separators=(",", ":")))
    print(f"model-usage/fireworks: {type(error).__name__}", file=sys.stderr)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
