import operator
from typing import Annotated, Sequence, TypedDict

from langchain_core.messages import BaseMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.graph.message import add_messages
from langgraph.managed import RemainingSteps
from langgraph.types import Command

VALID_SEVERITIES = {"nitpick", "suggestion", "issue", "blocker"}
VALID_STATUSES = {"approve", "request_changes", "comment"}


# Graph state
class ReviewState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    review_comments: Annotated[list[dict], operator.add]
    final_comment: str | None
    final_status: str | None
    # Required by create_react_agent to track steps remaining before recursion limit
    remaining_steps: RemainingSteps


# Output tools
@tool
def add_review_comment(
    file_path: str,
    line_number: int,
    comment: str,
    severity: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Queue an inline review comment on a specific file and line.

    Args:
        file_path: Relative path to the file, e.g. 'src/auth.py'.
        line_number: The line number the comment applies to (1-indexed).
        comment: The review comment text. Be concrete and actionable.
        severity: One of 'nitpick', 'suggestion', 'issue', or 'blocker'.
            nitpick: style or formatting preference, non-blocking.
            suggestion: improvement that would make code better, not required.
            issue: correctness, performance, or security concern.
            blocker: must be fixed before this PR can be merged.
    """
    if severity not in VALID_SEVERITIES:
        return Command(update={
            "messages": [ToolMessage(
                content=f"ERROR: severity must be one of {sorted(VALID_SEVERITIES)}",
                tool_call_id=tool_call_id,
            )],
        })

    entry = {
        "file_path": file_path,
        "line_number": int(line_number),
        "comment": comment,
        "severity": severity,
    }
    return Command(update={
        "review_comments": [entry],
        "messages": [ToolMessage(
            content=f"Comment queued ({severity} on {file_path}:{line_number}).",
            tool_call_id=tool_call_id,
        )],
    })


@tool
def submit_review(
    overall_comment: str,
    approval_status: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Finalize and submit the review. Call this exactly once when done reviewing.

    This is the terminal tool — the agent stops after calling it.

    Args:
        overall_comment: A summary of the review covering the main findings.
        approval_status: One of 'approve', 'request_changes', or 'comment'.
            approve: no issues found, PR is good to merge.
            request_changes: issues or blockers found, author must address them.
            comment: leaving notes without a formal approve/reject decision.
    """
    if approval_status not in VALID_STATUSES:
        return Command(update={
            "messages": [ToolMessage(
                content=f"ERROR: approval_status must be one of {sorted(VALID_STATUSES)}",
                tool_call_id=tool_call_id,
            )],
        })

    return Command(update={
        "final_comment": overall_comment,
        "final_status": approval_status,
        "messages": [ToolMessage(
            content=f"Review submitted with status '{approval_status}'.",
            tool_call_id=tool_call_id,
        )],
    })
