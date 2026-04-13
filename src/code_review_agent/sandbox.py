"""CodeBuild sandbox client for running tests and linters in isolation."""
import os
import time

import boto3

# Configurable via environment variables
CODEBUILD_PROJECT = os.getenv("CODEBUILD_PROJECT", "code-review-sandbox")
LOG_GROUP = os.getenv("CODEBUILD_LOG_GROUP", "/code-review/sandbox")
POLL_INTERVAL = 5      # seconds between build status checks
BUILD_TIMEOUT = 300    # 5 minutes — enough for any reasonable test suite
MAX_OUTPUT_BYTES = 4096


def run_in_sandbox(
    repo_url: str,
    head_branch: str,
    head_sha: str,
    tool: str,
    target_path: str | None = None,
    region: str = "us-east-1",
) -> str:
    """Run a tool (pytest or ruff) in a CodeBuild sandbox.

    Starts a CodeBuild job, polls until it finishes, then returns the
    last 4KB of CloudWatch log output — same interface as the local
    subprocess tools.

    Args:
        repo_url: HTTPS clone URL of the repository (e.g. https://github.com/owner/repo.git)
        head_branch: PR head branch name — cloned directly so the head SHA is present
        head_sha: Commit SHA to check out before running the tool
        tool: Either "pytest" or "ruff"
        target_path: Optional relative path to test file or directory to lint
        region: AWS region where the CodeBuild project is deployed

    Returns:
        Tool output as a string (truncated to MAX_OUTPUT_BYTES if necessary).
        Always returns a string — errors are returned as "ERROR: ..." strings.
    """
    if tool not in ("pytest", "ruff"):
        return f"ERROR: Invalid tool '{tool}'. Must be 'pytest' or 'ruff'."

    cb = boto3.client("codebuild", region_name=region)

    env_vars = [
        {"name": "REPO_URL", "value": repo_url, "type": "PLAINTEXT"},
        {"name": "HEAD_BRANCH", "value": head_branch, "type": "PLAINTEXT"},
        {"name": "HEAD_SHA", "value": head_sha, "type": "PLAINTEXT"},
        {"name": "TOOL", "value": tool, "type": "PLAINTEXT"},
        {"name": "TARGET_PATH", "value": target_path or "", "type": "PLAINTEXT"},
    ]

    # Start the build
    try:
        response = cb.start_build(
            projectName=CODEBUILD_PROJECT,
            environmentVariablesOverride=env_vars,
        )
    except cb.exceptions.ResourceNotFoundException:
        return (
            f"ERROR: CodeBuild project '{CODEBUILD_PROJECT}' not found. "
            "Deploy infrastructure/template.yaml first."
        )
    except Exception as e:
        return f"ERROR: Failed to start CodeBuild job: {e}"

    build_id = response["build"]["id"]

    # Poll until the build reaches a terminal state
    deadline = time.time() + BUILD_TIMEOUT
    build = None
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        try:
            builds = cb.batch_get_builds(ids=[build_id])["builds"]
            build = builds[0]
        except Exception as e:
            return f"ERROR: Failed to poll build status: {e}"

        status = build["buildStatus"]
        if status in ("SUCCEEDED", "FAILED", "FAULT", "STOPPED", "TIMED_OUT"):
            break
    else:
        return f"ERROR: Timed out waiting for CodeBuild job after {BUILD_TIMEOUT}s."

    # Fetch output from CloudWatch Logs
    log_info = build.get("logs", {})
    stream_name = log_info.get("streamName")
    group_name = log_info.get("groupName", LOG_GROUP)

    if not stream_name:
        return (
            f"ERROR: Build finished with status '{build['buildStatus']}' "
            "but no log stream found."
        )

    try:
        logs = boto3.client("logs", region_name=region)
        response = logs.get_log_events(
            logGroupName=group_name,
            logStreamName=stream_name,
            startFromHead=True,
        )
        output = "\n".join(e["message"] for e in response["events"])
    except Exception as e:
        return f"ERROR: Build finished but could not fetch logs: {e}"

    if not output.strip():
        return f"(Build finished with status '{build['buildStatus']}' — no output)"

    # Truncate from the front, keeping the tail (failures appear last)
    if len(output) > MAX_OUTPUT_BYTES:
        output = "[... truncated, showing last 4KB ...]\n" + output[-MAX_OUTPUT_BYTES:]

    return output
