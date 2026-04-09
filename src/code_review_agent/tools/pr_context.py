import json

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

MAX_BYTES = 4096


@tool
def get_pr_diff(config: RunnableConfig) -> str:
    """Return the unified diff of the pull request.

    Shows all lines added (+) and removed (-) across every changed file.
    Call this first to understand what the PR changes before exploring further.
    """
    diff_content = config["configurable"]["diff_content"]

    if len(diff_content) > MAX_BYTES:
        return (
            diff_content[:MAX_BYTES]
            + "\n... [diff truncated — use read_file to inspect specific files in full]"
        )
    return diff_content


@tool
def get_pr_metadata(config: RunnableConfig) -> str:
    """Return metadata about the pull request: title, author, branches, and files changed.

    Use this to understand the intent and scope of the PR before diving into the diff.
    """
    diff_content = config["configurable"]["diff_content"]

    # Parse changed files from diff --git lines (available in Stage 1 without GitHub API)
    files_changed = [
        line.split(" b/")[-1]
        for line in diff_content.splitlines()
        if line.startswith("diff --git ")
    ]

    metadata = {
        "title": config["configurable"].get("pr_title", "[Stage 1] PR under review"),
        "author": config["configurable"].get("pr_author", "unknown"),
        "base_branch": config["configurable"].get("base_branch", "main"),
        "head_branch": config["configurable"].get("head_branch", "unknown"),
        "files_changed": files_changed,
        "note": "Title/author/branches are stubs in Stage 1. Real metadata available in Stage 2.",
    }

    return json.dumps(metadata, indent=2)
