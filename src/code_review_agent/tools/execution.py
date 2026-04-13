import subprocess

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

MAX_BYTES = 4096


def _run_tests_local(repo_path: str, test_path: str | None) -> str:
    """Run pytest locally via subprocess."""
    target = f"{repo_path}/{test_path}" if test_path else repo_path
    try:
        result = subprocess.run(
            ["python", "-m", "pytest", target, "--tb=short", "-q", "--no-header"],
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=120,
        )
        output = result.stdout + result.stderr
        if not output.strip():
            return "pytest produced no output."
        if len(output) > MAX_BYTES:
            output = "[... truncated, showing last 4KB ...]\n" + output[-MAX_BYTES:]
        return output
    except FileNotFoundError:
        return "ERROR: pytest not found. Is it installed in the current environment?"
    except subprocess.TimeoutExpired:
        return "ERROR: pytest timed out after 120 seconds."


def _run_linter_local(repo_path: str, path: str | None) -> str:
    """Run ruff locally via subprocess."""
    target = f"{repo_path}/{path}" if path else repo_path
    try:
        result = subprocess.run(
            ["ruff", "check", target],
            capture_output=True,
            text=True,
            cwd=repo_path,
            timeout=60,
        )
        output = result.stdout + result.stderr
        if not output.strip():
            return "Linter found no issues."
        if len(output) > MAX_BYTES:
            output = output[:MAX_BYTES] + "\n... [truncated]"
        return output
    except FileNotFoundError:
        return "ERROR: ruff not found. Is it installed in the current environment?"
    except subprocess.TimeoutExpired:
        return "ERROR: ruff timed out after 60 seconds."


@tool
def run_tests(config: RunnableConfig, test_path: str | None = None) -> str:
    """Run the test suite using pytest and return the results.

    Args:
        test_path: Optional relative path to a specific test file or directory.
                   Omit to run the full test suite.
    """
    configurable = config["configurable"]

    if configurable.get("sandbox_mode"):
        # Stage 3: run in CodeBuild sandbox
        from code_review_agent.sandbox import run_in_sandbox

        repo_url = configurable.get("repo_url")
        head_branch = configurable.get("head_branch")
        head_sha = configurable.get("head_sha")
        if not repo_url or not head_branch or not head_sha:
            return "ERROR: sandbox_mode requires repo_url, head_branch, and head_sha in config."

        region = configurable.get("aws_region", "us-east-1")
        return run_in_sandbox(repo_url, head_branch, head_sha, "pytest", test_path, region)

    # Stage 1/2: run locally
    return _run_tests_local(configurable["repo_path"], test_path)


@tool
def run_linter(config: RunnableConfig, path: str | None = None) -> str:
    """Run ruff linter on the repository and return any warnings or errors.

    Args:
        path: Optional relative path to lint (file or directory).
              Omit to lint the entire repository.
    """
    configurable = config["configurable"]

    if configurable.get("sandbox_mode"):
        # Stage 3: run in CodeBuild sandbox
        from code_review_agent.sandbox import run_in_sandbox

        repo_url = configurable.get("repo_url")
        head_branch = configurable.get("head_branch")
        head_sha = configurable.get("head_sha")
        if not repo_url or not head_branch or not head_sha:
            return "ERROR: sandbox_mode requires repo_url, head_branch, and head_sha in config."

        region = configurable.get("aws_region", "us-east-1")
        return run_in_sandbox(repo_url, head_branch, head_sha, "ruff", path, region)

    # Stage 1/2: run locally
    return _run_linter_local(configurable["repo_path"], path)
