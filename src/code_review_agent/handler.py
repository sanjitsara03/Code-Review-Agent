"""AWS Lambda handler for GitHub pull_request webhook events.

Entry point: lambda_handler(event, context)

Flow:
  1. Verify GitHub webhook signature (HMAC-SHA256)
  2. Ignore events that aren't PR opened/reopened/synchronize
  3. Extract owner, repo, PR number from payload
  4. Load secrets from AWS Secrets Manager
  5. Fetch PR metadata + diff from GitHub
  6. Clone repo to /tmp at PR head SHA
  7. Run agent loop
  8. Post review back to GitHub
"""

import hashlib
import hmac
import json
import logging
import os
import shutil
import tarfile
import tempfile

import httpx

from code_review_agent.agent import run_review
from code_review_agent.github_client import GitHubClient
from code_review_agent.secrets import load_secrets
from code_review_agent.store import save_review

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# PR actions that should trigger a review
REVIEW_ACTIONS = {"opened", "reopened", "synchronize"}

#AWS Lambda entry point.
def lambda_handler(event: dict, context) -> dict:
    # Parse request
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    body_raw = event.get("body") or ""

    # Load secrets
    try:
        secrets = load_secrets()
    except Exception as e:
        logger.error("Failed to load secrets: %s", e)
        return _response(500, "Failed to load secrets")

    # Verify webhook signature
    sig_header = headers.get("x-hub-signature-256", "")
    if not _verify_signature(body_raw, sig_header, secrets["webhook_secret"]):
        logger.warning("Invalid webhook signature")
        return _response(401, "Invalid signature")

    # Parse payload
    try:
        payload = json.loads(body_raw)
    except json.JSONDecodeError:
        return _response(400, "Invalid JSON payload")

    action = payload.get("action")
    if action not in REVIEW_ACTIONS:
        logger.info("Ignoring action: %s", action)
        return _response(200, f"Ignored action: {action}")

    pr_data = payload.get("pull_request", {})
    repo_data = payload.get("repository", {})

    owner = repo_data.get("owner", {}).get("login")
    repo = repo_data.get("name")
    pr_number = pr_data.get("number")
    head_branch = pr_data.get("head", {}).get("ref")
    head_sha = pr_data.get("head", {}).get("sha")

    if not all([owner, repo, pr_number, head_branch, head_sha]):
        logger.error("Missing required fields in payload")
        return _response(400, "Missing required PR fields")

    logger.info("Reviewing %s/%s#%s (action=%s)", owner, repo, pr_number, action)

    # Fetch PR data from GitHub
    os.environ["GITHUB_TOKEN"] = secrets["github_token"]
    os.environ["ANTHROPIC_API_KEY"] = secrets["anthropic_api_key"]

    client = GitHubClient(token=secrets["github_token"])
    try:
        metadata = client.get_pr_metadata(owner, repo, pr_number)
        diff_content = client.get_pr_diff(owner, repo, pr_number)
    except ValueError as e:
        logger.error("Failed to fetch PR data: %s", e)
        return _response(500, f"Failed to fetch PR: {e}")

    # Download repo to /tmp
    temp_dir = tempfile.mkdtemp(prefix="code-review-", dir="/tmp")
    try:
        _download_github_repo(owner, repo, head_sha, secrets["github_token"], temp_dir)
    except RuntimeError as e:
        logger.error("Failed to download repo: %s", e)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return _response(500, f"Failed to download repo: {e}")

    # Run agent loop
    thread_id = f"lambda-{owner}-{repo}-{pr_number}-{head_sha[:8]}"
    aws_region = os.getenv("AWS_REGION", "us-east-2")
    start_time = __import__("time").time()

    try:
        result = run_review(
            repo_path=temp_dir,
            diff_content=diff_content,
            thread_id=thread_id,
            repo_url=metadata.repo_url,
            head_branch=head_branch,
            head_sha=head_sha,
            sandbox_mode=True,
            aws_region=aws_region,
        )
    except Exception as e:
        logger.error("Agent loop failed: %s", e)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return _response(500, f"Agent failed: {e}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    logger.info(
        "Review complete: status=%s, comments=%d",
        result["final_status"],
        len(result.get("review_comments", [])),
    )

    # Post review to GitHub
    try:
        review_url = client.post_review(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            overall_comment=result["final_comment"] or "",
            approval_status=result["final_status"] or "comment",
            inline_comments=result.get("review_comments"),
        )
        logger.info("Review posted: %s", review_url)
    except ValueError as e:
        logger.error("Failed to post review: %s", e)
        return _response(500, f"Failed to post review: {e}")

    # Save review record to DynamoDB
    try:
        save_review(
            owner=owner,
            repo=repo,
            pr_number=pr_number,
            head_sha=head_sha,
            status=result["final_status"] or "comment",
            comment_count=len(result.get("review_comments", [])),
            duration_ms=int(((__import__("time").time()) - start_time) * 1000),
            region=aws_region,
        )
    except Exception as e:
        logger.warning("Failed to save review record: %s", e)

    return _response(200, f"Review posted: {review_url}")

#Download a GitHub repo tarball and extract it into dest_dir.
def _download_github_repo(owner: str, repo: str, head_sha: str, token: str, dest_dir: str) -> None:
    
    url = f"https://api.github.com/repos/{owner}/{repo}/tarball/{head_sha}"
    try:
        with httpx.Client(follow_redirects=True, timeout=120) as client:
            response = client.get(url, headers={"Authorization": f"token {token}"})
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise RuntimeError(f"Failed to download repo tarball: {e}") from e

    tarball_path = os.path.join(dest_dir, "repo.tar.gz")
    with open(tarball_path, "wb") as f:
        f.write(response.content)

    try:
        with tarfile.open(tarball_path, "r:gz") as tar:
            members = tar.getmembers()
            # GitHub tarballs have a top-level directory like "owner-repo-sha/"
            # Strip it so dest_dir contains the repo root directly
            prefix = members[0].name.split("/")[0] + "/" if members else ""
            for member in members:
                if member.name.startswith(prefix):
                    member.name = member.name[len(prefix):]
                if member.name:
                    tar.extract(member, dest_dir)
    except tarfile.TarError as e:
        raise RuntimeError(f"Failed to extract repo tarball: {e}") from e
    finally:
        os.remove(tarball_path)


def _verify_signature(body: str, signature_header: str, secret: str) -> bool:
    """Verify GitHub's HMAC-SHA256 webhook signature.

    GitHub signs the raw request body with the webhook secret and sends
    the result in the X-Hub-Signature-256 header as "sha256=<hex>".
    We recompute it and compare with hmac.compare_digest to prevent
    timing attacks.
    """
    if not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(
        secret.encode(),
        body.encode(),
        hashlib.sha256,
    ).hexdigest()

    actual = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, actual)

#Build an API Gateway proxy response.
def _response(status_code: int, message: str) -> dict:
    
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"message": message}),
    }
