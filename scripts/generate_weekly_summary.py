#!/usr/bin/env python3
"""Generate sanitized weekly GitHub & Microsoft 365 activity summary.

Security & Privacy Guarantees (Public Repository Safe):
- Read-only queries to GitHub REST API and Microsoft Graph API (GET only).
- Supports Microsoft Entra OIDC (Federated Credentials) first, Client Secret fallback.
- Never prints or logs tokens, credentials, Tenant ID, Client ID, User ID, File ID,
  email addresses, domain names, or raw API responses (even on error).
- Only writes aggregated integer counts and availability statuses to logs/weekly-summary.md.
- If Microsoft Graph credentials or permissions are missing, outputs UNAVAILABLE (never fake data).
- Idempotent per ISO week (YYYY-Www) using a metrics fingerprint to prevent duplicate entries
  and avoid bot commit loops.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

VN_TZ = dt.timezone(dt.timedelta(hours=7), name="Asia/Ho_Chi_Minh")
SUMMARY_DEFAULT_PATH = Path("logs/weekly-summary.md")

# Patterns that MUST NEVER appear in public markdown output or logs
SENSITIVE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("JWT/Access Token", re.compile(r"eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}")),
    ("Bearer Token", re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/-]{12,}")),
    ("TempAuth Token", re.compile(r"(?i)tempauth=")),
    ("Email Address", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    (
        "UUID/GUID (Tenant/User/Client/File ID)",
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
    ),
    ("Tenant Domain", re.compile(r"(?i)[a-z0-9-]+\.onmicrosoft\.com")),
]


def mask_in_actions(secret_val: str | None) -> None:
    """Register secret value mask in GitHub Actions runner if non-empty."""
    if not secret_val or os.environ.get("GITHUB_ACTIONS") != "true":
        return
    for line in secret_val.splitlines():
        cleaned = line.strip()
        if len(cleaned) >= 4:
            print(f"::add-mask::{cleaned}", flush=True)


def assert_sanitized(content: str) -> None:
    """Raise ValueError if content contains any identifiable or sensitive pattern."""
    for label, pattern in SENSITIVE_PATTERNS:
        if pattern.search(content):
            raise ValueError(
                f"Sanitization check blocked output: detected forbidden pattern ({label})."
            )


def safe_http_get_json(
    url: str, headers: dict[str, str], timeout: int = 15
) -> tuple[int, dict[str, Any] | list[Any] | None]:
    """Perform an HTTP GET request and return (status_code, parsed_json_or_none).

    Never logs URL query parameters, headers, or response bodies on error.
    """
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
            raw = resp.read().decode("utf-8", errors="replace")
            return status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception:
        return 0, None


def detect_github_repo() -> str:
    """Return 'owner/repo' from GITHUB_REPOSITORY env or git remote.origin.url."""
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if repo and "/" in repo:
        return repo
    try:
        proc = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            check=True,
        )
        remote_url = proc.stdout.strip()
        match = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", remote_url)
        if match:
            return match.group(1)
    except Exception:
        pass
    return ""


def collect_github_metrics(
    window_start_utc: dt.datetime,
    window_end_utc: dt.datetime,
) -> dict[str, str]:
    """Collect 7-day GitHub repository activity metrics."""
    repo = detect_github_repo()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    mask_in_actions(token)

    since_iso = window_start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    until_iso = window_end_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "m365-graph-sandbox-activity-tracker",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    dev_commits: int | None = None
    bot_commits: int | None = None
    active_days: int | None = None
    push_events: str = "UNAVAILABLE"
    workflow_runs: str = "UNAVAILABLE"
    source_label = "UNAVAILABLE"

    if repo and "/" in repo:
        # 1. Query commits via GitHub REST API
        commits_url = (
            f"https://api.github.com/repos/{repo}/commits"
            f"?since={urllib.parse.quote(since_iso)}&until={urllib.parse.quote(until_iso)}&per_page=100"
        )
        status, data = safe_http_get_json(commits_url, headers)
        if status == 200 and isinstance(data, list):
            source_label = "GitHub REST API"
            dev_count = 0
            bot_count = 0
            days_set: set[str] = set()
            for item in data:
                if not isinstance(item, dict):
                    continue
                commit_obj = item.get("commit") or {}
                author_obj = commit_obj.get("author") or {}
                author_name = str(author_obj.get("name") or "").lower()
                msg = str(commit_obj.get("message") or "")
                date_str = str(author_obj.get("date") or "")[:10]
                is_bot = (
                    "github-actions[bot]" in author_name
                    or "[skip ci]" in msg.lower()
                    or msg.startswith("chore(summary):")
                    or msg.startswith("docs(log):")
                )
                if is_bot:
                    bot_count += 1
                else:
                    dev_count += 1
                    if len(date_str) == 10:
                        days_set.add(date_str)
            dev_commits = dev_count
            bot_commits = bot_count
            active_days = len(days_set)

        # 2. Query repository push activity in window (/activity endpoint, fallback to /events)
        activity_url = f"https://api.github.com/repos/{repo}/activity?per_page=100"
        act_status, act_data = safe_http_get_json(activity_url, headers)
        if act_status == 200 and isinstance(act_data, list):
            pushes = 0
            for act in act_data:
                if not isinstance(act, dict):
                    continue
                act_type = str(act.get("activity_type") or "")
                if act_type not in ("push", "branch_creation"):
                    continue
                ts = str(act.get("timestamp") or "")
                if ts >= since_iso and ts <= until_iso:
                    actor = str((act.get("actor") or {}).get("login") or "").lower()
                    if "github-actions" not in actor:
                        pushes += 1
            push_events = str(pushes)
        else:
            events_url = f"https://api.github.com/repos/{repo}/events?per_page=100"
            ev_status, ev_data = safe_http_get_json(events_url, headers)
            if ev_status == 200 and isinstance(ev_data, list):
                pushes = 0
                for ev in ev_data:
                    if not isinstance(ev, dict) or ev.get("type") != "PushEvent":
                        continue
                    created_at = str(ev.get("created_at") or "")
                    if created_at >= since_iso and created_at <= until_iso:
                        actor = str((ev.get("actor") or {}).get("login") or "").lower()
                        if "github-actions" not in actor:
                            pushes += 1
                push_events = str(pushes)

        # 3. Query Actions workflow runs in window (excluding this summary workflow itself to prevent fingerprint loops)
        created_range = f"{since_iso}..{until_iso}"
        runs_url = (
            f"https://api.github.com/repos/{repo}/actions/runs"
            f"?created={urllib.parse.quote(created_range)}&per_page=100"
        )
        run_status, run_data = safe_http_get_json(runs_url, headers)
        if run_status == 200 and isinstance(run_data, dict):
            runs_list = run_data.get("workflow_runs")
            if isinstance(runs_list, list):
                current_run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
                total_runs = 0
                success_runs = 0
                failed_runs = 0
                for r in runs_list:
                    if not isinstance(r, dict):
                        continue
                    if current_run_id and str(r.get("id")) == current_run_id:
                        continue
                    wf_path = str(r.get("path") or "")
                    wf_name = str(r.get("name") or "")
                    if (
                        wf_path.endswith("weekly-activity-summary.yml")
                        or wf_name == "Weekly GitHub & M365 Activity Summary"
                    ):
                        continue
                    if r.get("status") != "completed":
                        continue
                    total_runs += 1
                    conclusion = str(r.get("conclusion") or "")
                    if conclusion == "success":
                        success_runs += 1
                    elif conclusion in ("failure", "timed_out", "cancelled"):
                        failed_runs += 1
                workflow_runs = (
                    f"{total_runs} completed ({success_runs} success, {failed_runs} failed/other)"
                )

    # Fallback to local Git history if REST API was not used/reachable
    if dev_commits is None:
        try:
            proc = subprocess.run(
                [
                    "git",
                    "log",
                    f"--since={since_iso}",
                    f"--until={until_iso}",
                    "--format=%an|%ad|%s",
                    "--date=short",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            source_label = "Local Git History"
            dev_count = 0
            bot_count = 0
            days_set = set()
            for line in proc.stdout.splitlines():
                parts = line.split("|", 2)
                if len(parts) != 3:
                    continue
                author_name, date_str, msg = parts[0].lower(), parts[1].strip(), parts[2]
                is_bot = (
                    "github-actions[bot]" in author_name
                    or "[skip ci]" in msg.lower()
                    or msg.startswith("chore(summary):")
                    or msg.startswith("docs(log):")
                )
                if is_bot:
                    bot_count += 1
                else:
                    dev_count += 1
                    if len(date_str) == 10:
                        days_set.add(date_str)
            dev_commits = dev_count
            bot_commits = bot_count
            active_days = len(days_set)
        except Exception:
            pass

    return {
        "source": source_label,
        "dev_commits": str(dev_commits) if dev_commits is not None else "UNAVAILABLE",
        "bot_commits": str(bot_commits) if bot_commits is not None else "UNAVAILABLE",
        "active_days": str(active_days) if active_days is not None else "UNAVAILABLE",
        "push_events": push_events,
        "workflow_runs": workflow_runs,
    }


def acquire_graph_token() -> tuple[str | None, str]:
    """Acquire Microsoft Graph access token via OIDC (preferred) or Client Secret.

    Returns (access_token_or_none, auth_mode_description).
    Never prints credentials or error responses.
    """
    tenant_id = os.environ.get("AZURE_TENANT_ID", "").strip()
    client_id = os.environ.get("AZURE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("AZURE_CLIENT_SECRET", "").strip()

    mask_in_actions(tenant_id)
    mask_in_actions(client_id)
    mask_in_actions(client_secret)

    if not tenant_id or not client_id:
        return None, "UNAVAILABLE (Credentials not configured)"

    token_endpoint = f"https://login.microsoftonline.com/{urllib.parse.quote(tenant_id)}/oauth2/v2.0/token"

    # 1. Try GitHub Actions OIDC Federated Credential exchange first
    oidc_req_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "").strip()
    oidc_req_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "").strip()
    mask_in_actions(oidc_req_token)

    if oidc_req_url and oidc_req_token:
        sep = "&" if "?" in oidc_req_url else "?"
        audience_url = f"{oidc_req_url}{sep}audience=api://AzureADTokenExchange"
        status, oidc_data = safe_http_get_json(
            audience_url,
            headers={
                "Authorization": f"Bearer {oidc_req_token}",
                "Accept": "application/json",
                "User-Agent": "m365-graph-sandbox-activity-tracker",
            },
        )
        federated_jwt = (
            str(oidc_data.get("value") or "").strip()
            if status == 200 and isinstance(oidc_data, dict)
            else ""
        )
        if federated_jwt:
            mask_in_actions(federated_jwt)
            body = urllib.parse.urlencode(
                {
                    "client_id": client_id,
                    "scope": "https://graph.microsoft.com/.default",
                    "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                    "client_assertion": federated_jwt,
                    "grant_type": "client_credentials",
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                token_endpoint,
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.getcode() == 200:
                        payload = json.loads(resp.read().decode("utf-8", errors="replace"))
                        access_token = str(payload.get("access_token") or "").strip()
                        if access_token:
                            mask_in_actions(access_token)
                            return access_token, "OIDC Federated Credentials"
            except Exception:
                pass

    # 2. Fallback to Client Secret if provided
    if client_secret:
        body = urllib.parse.urlencode(
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            token_endpoint,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.getcode() == 200:
                    payload = json.loads(resp.read().decode("utf-8", errors="replace"))
                    access_token = str(payload.get("access_token") or "").strip()
                    if access_token:
                        mask_in_actions(access_token)
                        return access_token, "GitHub Secrets (Client Credentials)"
        except Exception:
            return None, "UNAVAILABLE (Token exchange failed)"

    return None, "UNAVAILABLE (No valid OIDC assertion or Client Secret)"


def collect_m365_metrics(window_start_utc: dt.datetime) -> dict[str, str]:
    """Collect sanitized, read-only Microsoft 365 Graph & Audit counts."""
    token, auth_mode = acquire_graph_token()
    if not token:
        return {
            "auth_mode": auth_mode,
            "users_count": "UNAVAILABLE",
            "groups_count": "UNAVAILABLE",
            "apps_count": "UNAVAILABLE",
            "directory_audits_7d": "UNAVAILABLE",
            "signin_audits_7d": "UNAVAILABLE",
        }

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "m365-graph-sandbox-activity-tracker",
    }

    def count_collection(endpoint: str) -> str:
        status, payload = safe_http_get_json(endpoint, headers)
        if status == 200 and isinstance(payload, dict) and isinstance(payload.get("value"), list):
            return str(len(payload["value"]))
        if status in (401, 403):
            return "UNAVAILABLE (Permission not granted)"
        return "UNAVAILABLE"

    users_count = count_collection("https://graph.microsoft.com/v1.0/users?$top=999&$select=id")
    groups_count = count_collection("https://graph.microsoft.com/v1.0/groups?$top=999&$select=id")
    apps_count = count_collection(
        "https://graph.microsoft.com/v1.0/applications?$top=999&$select=id"
    )

    since_iso = window_start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    dir_filter = urllib.parse.quote(f"activityDateTime ge {since_iso}")
    dir_audit_url = (
        f"https://graph.microsoft.com/v1.0/auditLogs/directoryAudits"
        f"?$filter={dir_filter}&$top=999&$select=category,result"
    )
    da_status, da_payload = safe_http_get_json(dir_audit_url, headers)
    if (
        da_status == 200
        and isinstance(da_payload, dict)
        and isinstance(da_payload.get("value"), list)
    ):
        events = da_payload["value"]
        total_ev = len(events)
        success_ev = sum(
            1
            for e in events
            if isinstance(e, dict) and str(e.get("result") or "").lower() == "success"
        )
        directory_audits_7d = f"{total_ev} events ({success_ev} success)"
    elif da_status in (401, 403):
        directory_audits_7d = "UNAVAILABLE (Requires AuditLog.Read.All)"
    else:
        directory_audits_7d = "UNAVAILABLE"

    signin_filter = urllib.parse.quote(f"createdDateTime ge {since_iso}")
    signin_url = (
        f"https://graph.microsoft.com/v1.0/auditLogs/signIns"
        f"?$filter={signin_filter}&$top=999&$select=id"
    )
    si_status, si_payload = safe_http_get_json(signin_url, headers)
    if (
        si_status == 200
        and isinstance(si_payload, dict)
        and isinstance(si_payload.get("value"), list)
    ):
        signin_audits_7d = f"{len(si_payload['value'])} events"
    elif si_status in (401, 403):
        signin_audits_7d = "UNAVAILABLE (Requires AuditLog.Read.All)"
    else:
        signin_audits_7d = "UNAVAILABLE"

    return {
        "auth_mode": auth_mode,
        "users_count": users_count,
        "groups_count": groups_count,
        "apps_count": apps_count,
        "directory_audits_7d": directory_audits_7d,
        "signin_audits_7d": signin_audits_7d,
    }


def render_week_block(
    week_id: str,
    window_start_vn: dt.datetime,
    window_end_vn: dt.datetime,
    gh_metrics: dict[str, str],
    m365_metrics: dict[str, str],
) -> tuple[str, str]:
    """Render sanitized markdown block for a week and return (fingerprint, block_markdown)."""
    fingerprint_payload = json.dumps(
        {"week": week_id, "github": gh_metrics, "m365": m365_metrics},
        sort_keys=True,
    )
    fingerprint = hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()[:12]

    period_str = (
        f"{window_start_vn.strftime('%Y-%m-%d')} to {window_end_vn.strftime('%Y-%m-%d')} "
        f"(Asia/Ho_Chi_Minh)"
    )
    generated_str = f"{window_end_vn.strftime('%Y-%m-%d %H:%M')} (Asia/Ho_Chi_Minh)"

    block = f"""<!-- WEEK:{week_id} -->
<!-- FINGERPRINT:{fingerprint} -->
## Week {week_id}

- **Reporting Window:** {period_str}
- **Last Updated:** {generated_str}
- **Project:** Zoka Graph Test App (`m365-graph-sandbox-test`)
- **Privacy Level:** Public-Safe Aggregated Summary (PII & IDs Stripped)

### 1. GitHub Repository Activity (7-Day Window)

| Metric | Value |
| :--- | :--- |
| Data Source | {gh_metrics['source']} |
| Developer Commits | {gh_metrics['dev_commits']} |
| Automated Log Commits | {gh_metrics['bot_commits']} |
| Active Commit Days | {gh_metrics['active_days']} |
| Push Events (Public API) | {gh_metrics['push_events']} |
| Workflow Runs Completed | {gh_metrics['workflow_runs']} |

### 2. Microsoft 365 Sandbox & Graph Activity (Read-Only)

| Metric | Status / Aggregated Count |
| :--- | :--- |
| Authentication Mode | {m365_metrics['auth_mode']} |
| Directory Users (Count Only) | {m365_metrics['users_count']} |
| Directory Groups (Count Only) | {m365_metrics['groups_count']} |
| Entra App Registrations (Count Only) | {m365_metrics['apps_count']} |
| Directory Audit Events (7d) | {m365_metrics['directory_audits_7d']} |
| Sign-In Audit Events (7d) | {m365_metrics['signin_audits_7d']} |
<!-- /WEEK:{week_id} -->"""

    assert_sanitized(block)
    return fingerprint, block


def update_summary_file(summary_path: Path, week_id: str, fingerprint: str, block: str) -> bool:
    """Update logs/weekly-summary.md idempotently. Returns True if file changed."""
    header = """# Weekly GitHub & Microsoft 365 Sandbox Activity Summary

Báo cáo tổng hợp tự động hằng tuần cho dự án **Zoka Graph Test App** (`m365-graph-sandbox-test`).

> **Chính sách bảo mật (Public Repository):**
> - Chỉ lưu số liệu thống kê tổng hợp (số lượng commit, workflow runs, số lượng sự kiện Graph/Audit).
> - Không lưu dữ liệu gốc, API response, Access Token, Client Secret, Tenant ID, User ID, File ID, hoặc địa chỉ email.
> - Các hạng mục Microsoft Graph chưa được cấp quyền hoặc chưa cấu hình xác thực sẽ hiển thị `UNAVAILABLE`, tuyệt đối không tạo dữ liệu giả.
"""

    if not summary_path.exists() or summary_path.stat().st_size == 0:
        full_content = f"{header}\n{block}\n"
        assert_sanitized(full_content)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(full_content, encoding="utf-8")
        return True

    existing = summary_path.read_text(encoding="utf-8")
    week_pattern = re.compile(
        rf"<!-- WEEK:{re.escape(week_id)} -->.*?<!-- /WEEK:{re.escape(week_id)} -->",
        re.DOTALL,
    )
    match = week_pattern.search(existing)
    if match:
        existing_block = match.group(0)
        if f"<!-- FINGERPRINT:{fingerprint} -->" in existing_block:
            return False
        updated = existing[: match.start()] + block + existing[match.end() :]
    else:
        sep = "\n" if existing.endswith("\n") else "\n\n"
        updated = f"{existing}{sep}{block}\n"

    assert_sanitized(updated)
    summary_path.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate sanitized weekly GitHub & Microsoft 365 activity summary."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=SUMMARY_DEFAULT_PATH,
        help="Path to weekly summary markdown file (default: logs/weekly-summary.md).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print preview only without modifying the output file.",
    )
    args = parser.parse_args()

    now_utc = dt.datetime.now(dt.timezone.utc)
    now_vn = now_utc.astimezone(VN_TZ)
    window_start_vn = now_vn - dt.timedelta(days=7)
    window_start_utc = window_start_vn.astimezone(dt.timezone.utc)

    iso_year, iso_week, _ = now_vn.isocalendar()
    week_id = f"{iso_year}-W{iso_week:02d}"

    gh_metrics = collect_github_metrics(window_start_utc, now_utc)
    m365_metrics = collect_m365_metrics(window_start_utc)

    fingerprint, block = render_week_block(
        week_id=week_id,
        window_start_vn=window_start_vn,
        window_end_vn=now_vn,
        gh_metrics=gh_metrics,
        m365_metrics=m365_metrics,
    )

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if step_summary:
        try:
            with open(step_summary, "a", encoding="utf-8") as sf:
                sf.write("### Weekly Activity Summary Preview\n\n")
                sf.write(block + "\n")
        except Exception:
            pass

    if args.dry_run:
        print(block)
        return 0

    changed = update_summary_file(args.output, week_id, fingerprint, block)
    if changed:
        print(f"Updated {args.output} for week {week_id} (fingerprint: {fingerprint}).")
    else:
        print(f"No metric changes for week {week_id} (fingerprint: {fingerprint}); skipping write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
