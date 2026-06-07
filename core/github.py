"""BAW — GitHub Integration

Manage repos, issues, PRs, and CI via gh CLI or REST API.
Supports both authenticated and read-only operations.
"""

from __future__ import annotations
import os, sys, json, subprocess as sp
from pathlib import Path
from typing import Optional


# ── Auth detection ──

def _check_gh_auth() -> bool:
    """Check if gh CLI is authenticated."""
    r = sp.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=10)
    return r.returncode == 0


def _get_token() -> str | None:
    """Get GitHub token from env or gh."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    if _check_gh_auth():
        r = sp.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return r.stdout.strip()
    return None


def _run_gh(args: list[str], timeout: int = 30) -> dict:
    """Run a gh CLI command and return parsed JSON result."""
    cmd = ["gh"] + args + ["--json", "number,title,state,createdAt,updatedAt,url,author"]
    if "--jq" not in args and "--json" not in args:
        cmd += ["--json", "number,title,state,createdAt,updatedAt,url,author"]
    try:
        r = sp.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return {"error": r.stderr.strip(), "data": None}
        return {"error": None, "data": json.loads(r.stdout) if r.stdout.strip() else []}
    except sp.TimeoutExpired:
        return {"error": "gh command timed out", "data": None}
    except json.JSONDecodeError:
        return {"error": "failed to parse gh output", "data": r.stdout}
    except FileNotFoundError:
        return {"error": "gh CLI not found. Install: https://cli.github.com/", "data": None}


# ── Repos ──

def list_repos(owner: str = "") -> list[dict]:
    """List GitHub repos, optionally filtered by owner."""
    if not _check_gh_auth():
        fallback = _fallback_repos(owner)
        return fallback

    result = _run_gh(["repo", "list", owner, "--limit", "20", "--json",
                       "name,owner,description,updatedAt,isFork,primaryLanguage"])
    if result["error"]:
        return [{"error": result["error"]}]
    data = result["data"]
    if isinstance(data, list):
        return data
    return [{"name": str(data)}]


def _fallback_repos(owner: str = "") -> list[dict]:
    """Read-only fallback: list repos from public API."""
    import httpx
    url = f"https://api.github.com/users/{owner}/repos" if owner else "https://api.github.com/user/repos"
    try:
        headers = {}
        token = _get_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = httpx.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            return [{"name": r["name"], "owner": r["owner"]["login"],
                     "description": r.get("description") or "",
                     "updatedAt": r.get("updated_at") or ""} for r in resp.json()[:20]]
        return [{"error": f"HTTP {resp.status_code}: {resp.text[:100]}"}]
    except Exception as e:
        return [{"error": str(e)}]


# ── Issues ──

def list_issues(repo: str, state: str = "open", label: str = "") -> list[dict]:
    """List issues in a repo."""
    args = ["issue", "list", "--repo", repo, "--state", state, "--limit", "20"]
    if label:
        args += ["--label", label]
    result = _run_gh(args)
    if result["error"]:
        return [{"error": result["error"]}]
    return result["data"] if isinstance(result["data"], list) else []


def create_issue(repo: str, title: str, body: str = "", label: str = "") -> dict:
    """Create a new issue."""
    if not _check_gh_auth():
        return {"error": "gh not authenticated. Run: gh auth login"}
    cmd = ["issue", "create", "--repo", repo, "--title", title, "--body", body]
    if label:
        cmd += ["--label", label]
    try:
        r = sp.run(["gh"] + cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return {"error": r.stderr.strip()}
        return {"url": r.stdout.strip(), "title": title, "repo": repo}
    except Exception as e:
        return {"error": str(e)}


def close_issue(repo: str, number: int) -> dict:
    """Close an issue."""
    if not _check_gh_auth():
        return {"error": "gh not authenticated"}
    r = sp.run(["gh", "issue", "close", str(number), "--repo", repo],
               capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        return {"error": r.stderr.strip()}
    return {"closed": True, "repo": repo, "number": number}


# ── Pull Requests ──

def list_prs(repo: str, state: str = "open") -> list[dict]:
    """List pull requests."""
    args = ["pr", "list", "--repo", repo, "--state", state, "--limit", "20"]
    result = _run_gh(args)
    if result["error"]:
        return [{"error": result["error"]}]
    return result["data"] if isinstance(result["data"], list) else []


def pr_status(repo: str, number: int) -> dict:
    """Check PR status (CI checks, mergeable state)."""
    if not _check_gh_auth():
        return {"error": "gh not authenticated"}
    r = sp.run(
        ["gh", "pr", "view", str(number), "--repo", repo,
         "--json", "number,title,state,mergeable,mergeStateStatus,reviews,additions,deletions,headRefName,baseRefName,statusCheckRollup"],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        return {"error": r.stderr.strip()}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"error": "failed to parse output"}


# ── CI / Workflows ──

def list_workflows(repo: str) -> list[dict]:
    """List GitHub Actions workflows."""
    if not _check_gh_auth():
        return [{"error": "gh not authenticated"}]
    r = sp.run(
        ["gh", "workflow", "list", "--repo", repo, "--limit", "20",
         "--json", "id,name,state,path"],
        capture_output=True, text=True, timeout=15,
    )
    if r.returncode != 0:
        return [{"error": r.stderr.strip()}]
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return [{"error": "failed to parse"}]


def list_runs(repo: str, branch: str = "", limit: int = 10) -> list[dict]:
    """List recent workflow runs."""
    if not _check_gh_auth():
        return [{"error": "gh not authenticated"}]
    cmd = ["run", "list", "--repo", repo, "--limit", str(limit),
           "--json", "databaseId,displayTitle,status,conclusion,createdAt,updatedAt,headBranch,event"]
    if branch:
        cmd += ["--branch", branch]
    result = _run_gh(cmd)
    if result["error"]:
        return [{"error": result["error"]}]
    return result["data"] if isinstance(result["data"], list) else []


# ── BAW repo shortcuts ──

BAW_REPO = "cornreform/baw-agent-platform"


def baw_status() -> str:
    """Check BAW's own repo status: open issues, PRs, CI."""
    parts = []
    issues = list_issues(BAW_REPO, state="open")
    if issues and not issues[0].get("error"):
        parts.append(f"  📋 Open issues: {len(issues)}")
        for i in issues[:5]:
            parts.append(f"    #{i.get('number', '?')} {i.get('title', '?')[:60]}")
    else:
        parts.append(f"  📋 Issues: unable to fetch")

    prs = list_prs(BAW_REPO, state="open")
    if prs and not prs[0].get("error"):
        parts.append(f"  🔀 Open PRs: {len(prs)}")
        for p in prs[:3]:
            parts.append(f"    #{p.get('number', '?')} {p.get('title', '?')[:60]}")
    else:
        parts.append(f"  🔀 PRs: unable to fetch")

    runs = list_runs(BAW_REPO, limit=5)
    if runs and not runs[0].get("error"):
        parts.append(f"  🔄 Recent CI runs: {len(runs)}")
        for r in runs[:3]:
            status = r.get("conclusion", r.get("status", "?"))
            parts.append(f"    {r.get('displayTitle', '?')[:40]} — {status}")
    else:
        parts.append(f"  🔄 CI: unable to fetch")

    return "\n".join(parts)


# ── Setup guide ──

def setup_guide() -> str:
    """Show how to set up GitHub authentication."""
    lines = [
        "🌐 GitHub Integration Setup",
        "",
        "BAW uses `gh` CLI for GitHub operations.",
        "",
        "1. Install gh:",
        "   https://cli.github.com/",
        "",
        "2. Authenticate:",
        "   gh auth login",
        "",
        "3. Or set a token directly:",
        "   export GITHUB_TOKEN='ghp_xxx'",
        "   # Add this to ~/.baw/.env",
        "",
        "Current status:",
    ]
    if _check_gh_auth():
        lines.append("   ✅ gh is authenticated")
        try:
            r = sp.run(["gh", "api", "user", "--jq", ".login"],
                       capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                lines.append(f"   Logged in as: {r.stdout.strip()}")
        except Exception:
            pass
    else:
        lines.append("   ❌ gh not authenticated")
        token = _get_token()
        if token:
            lines.append("   ✅ GITHUB_TOKEN found (read-only)")
        else:
            lines.append("   ❌ No token found — read-only public API only")
    return "\n".join(lines)
