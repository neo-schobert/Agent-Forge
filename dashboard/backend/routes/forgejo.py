"""
Forgejo proxy routes.

GET  /api/prs                        – list pull requests
GET  /api/prs/{pr_number}            – PR details + diff
POST /api/prs/{pr_number}/merge      – merge a PR
POST /api/prs/{pr_number}/comment    – add comment to PR
GET  /api/issues                     – list issues
GET  /api/issues/{issue_number}      – issue details
"""
from __future__ import annotations

from typing import Optional

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from config import config

log = structlog.get_logger(__name__)
router = APIRouter(tags=["forgejo"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _headers() -> dict:
    return {**config.forgejo_auth_headers(), "Content-Type": "application/json"}


def _owner() -> str:
    return config.forgejo_admin_user or "agentforge"


def _repo() -> str:
    return config.forgejo_workspace_repo


async def _forgejo_get(path: str) -> dict | list:
    url = f"{config.forgejo_api_base}{path}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=_headers())
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Forgejo unreachable: {exc}") from exc

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Not found in Forgejo")
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=resp.status_code, detail=resp.text[:300])
    return resp.json()


def _normalise_pr(pr: dict) -> dict:
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    user = pr.get("user") or {}
    return {
        "id": pr.get("id"),
        "number": pr.get("number"),
        "title": pr.get("title", ""),
        "body": pr.get("body", ""),
        "state": pr.get("state", ""),
        "branch": head.get("label") or head.get("ref"),
        "base": base.get("label") or base.get("ref"),
        "created_at": pr.get("created_at"),
        "html_url": pr.get("html_url"),
        "user": {"login": user.get("login"), "avatar_url": user.get("avatar_url")},
        "files_changed": pr.get("changed_files"),
        "mergeable": pr.get("mergeable"),
        "merged": pr.get("merged"),
    }


# ---------------------------------------------------------------------------
# Pull Request routes
# ---------------------------------------------------------------------------


@router.get("/api/prs")
async def list_prs(
    state: str = Query(default="open"),
    limit: int = Query(default=20, ge=1, le=50),
    page: int = Query(default=1, ge=1),
):
    """List pull requests from the Forgejo workspace repo."""
    owner, repo = _owner(), _repo()
    path = f"/repos/{owner}/{repo}/pulls?state={state}&limit={limit}&page={page}"
    prs = await _forgejo_get(path)
    assert isinstance(prs, list)
    return {"prs": [_normalise_pr(pr) for pr in prs]}


@router.get("/api/prs/{pr_number}")
async def get_pr(pr_number: int):
    """Get PR details plus the raw diff."""
    owner, repo = _owner(), _repo()
    pr_data = await _forgejo_get(f"/repos/{owner}/{repo}/pulls/{pr_number}")
    assert isinstance(pr_data, dict)

    # Fetch diff
    diff_url = f"{config.forgejo_api_base}/repos/{owner}/{repo}/pulls/{pr_number}.diff"
    diff_content: Optional[str] = None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            diff_resp = await client.get(diff_url, headers=config.forgejo_auth_headers())
        if diff_resp.status_code == 200:
            diff_content = diff_resp.text
    except httpx.RequestError as exc:
        log.warning("pr_diff_fetch_failed", pr=pr_number, error=str(exc))

    result = _normalise_pr(pr_data)
    result["diff"] = diff_content
    return result


class MergeBody(BaseModel):
    message: str = ""
    merge_style: str = "merge"  # merge | squash | rebase


@router.post("/api/prs/{pr_number}/merge")
async def merge_pr(pr_number: int, payload: MergeBody):
    """Merge a pull request."""
    if payload.merge_style not in ("merge", "squash", "rebase"):
        raise HTTPException(status_code=400, detail="merge_style must be merge, squash or rebase")

    owner, repo = _owner(), _repo()
    url = f"{config.forgejo_api_base}/repos/{owner}/{repo}/pulls/{pr_number}/merge"

    body: dict = {"Do": payload.merge_style}
    if payload.message:
        body["commit_message"] = payload.message

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, headers=_headers(), json=body)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Forgejo unreachable: {exc}") from exc

    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="PR not found")
    if resp.status_code not in (200, 204):
        raise HTTPException(status_code=resp.status_code, detail=resp.text[:300])

    return {"merged": True, "pr_number": pr_number}


class CommentBody(BaseModel):
    body: str


@router.post("/api/prs/{pr_number}/comment")
async def comment_on_pr(pr_number: int, payload: CommentBody):
    """Add a comment to a PR (via the issues comment endpoint)."""
    owner, repo = _owner(), _repo()
    url = f"{config.forgejo_api_base}/repos/{owner}/{repo}/issues/{pr_number}/comments"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, headers=_headers(), json={"body": payload.body})
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Forgejo unreachable: {exc}") from exc

    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=resp.status_code, detail=resp.text[:300])

    return resp.json()


# ---------------------------------------------------------------------------
# Issues routes
# ---------------------------------------------------------------------------


@router.get("/api/issues")
async def list_issues(
    state: str = Query(default="open"),
    limit: int = Query(default=20, ge=1, le=50),
    page: int = Query(default=1, ge=1),
    label: Optional[str] = Query(default=None),
):
    """List issues from the Forgejo workspace repo."""
    owner, repo = _owner(), _repo()
    params = f"?state={state}&limit={limit}&page={page}&type=issues"
    if label:
        params += f"&labels={label}"
    issues = await _forgejo_get(f"/repos/{owner}/{repo}/issues{params}")
    return {"issues": issues}


@router.get("/api/issues/{issue_number}")
async def get_issue(issue_number: int):
    """Get a single issue by number."""
    owner, repo = _owner(), _repo()
    issue = await _forgejo_get(f"/repos/{owner}/{repo}/issues/{issue_number}")
    return issue
