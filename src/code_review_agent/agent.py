from langchain_anthropic import ChatAnthropic
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import create_react_agent

from code_review_agent.tools import ALL_TOOLS, ReviewState

# Haiku for tool-calling steps — fast and cheap (see CLAUDE.md cost controls)
MODEL = "claude-haiku-4-5-20251001"

# Hard cap: 25 tool calls per review (see CLAUDE.md cost controls)
RECURSION_LIMIT = 25

SYSTEM_PROMPT = """You are an expert code reviewer. Your job is to produce a thorough, \
actionable review of a pull request.

Follow this process in order:
1. Call get_pr_diff() to understand what changed.
2. Call get_pr_metadata() for context (title, author, files changed).
3. Explore relevant files with read_file, list_directory, search_code, and find_file \
to understand the surrounding code.
4. Call run_linter() to surface style and correctness issues.
5. Call run_tests() to check whether the change breaks anything.
6. Queue specific inline comments with add_review_comment(). Assign the correct severity:
   - nitpick: style or formatting preference, non-blocking
   - suggestion: improvement that would make code better, not required
   - issue: correctness, performance, or security concern
   - blocker: must be fixed before this PR can be merged
7. Call submit_review() with an overall summary and your approval decision.

Rules:
- Be concrete. Always reference file paths and line numbers.
- Do not approve if there are any blockers or issues — use request_changes.
- You MUST call submit_review() to finish. Never stop without it.
- Do not repeat yourself. One comment per finding.
"""


def build_graph():
    """Build and compile the code review agent graph."""
    model = ChatAnthropic(model=MODEL)

    graph = create_react_agent(
        model=model,
        tools=ALL_TOOLS,
        prompt=SYSTEM_PROMPT,
        state_schema=ReviewState,
        checkpointer=InMemorySaver(),
    )

    return graph


def run_review(repo_path: str, diff_content: str, thread_id: str) -> dict:
    """Run a full code review and return the final state.

    Args:
        repo_path: Absolute path to the locally cloned repository.
        diff_content: Unified diff string of the PR changes.
        thread_id: Unique ID for this review run (used by checkpointer).

    Returns:
        dict with keys: final_status, final_comment, review_comments.
    """
    graph = build_graph()

    initial_state = {
        "messages": [("user", "Please review this pull request.")],
        "review_comments": [],
        "final_comment": None,
        "final_status": None,
    }

    config = {
        "configurable": {
            "thread_id": thread_id,
            "repo_path": repo_path,
            "diff_content": diff_content,
        },
        "recursion_limit": RECURSION_LIMIT,
    }

    final_state = graph.invoke(initial_state, config=config)

    return {
        "final_status": final_state.get("final_status"),
        "final_comment": final_state.get("final_comment"),
        "review_comments": final_state.get("review_comments", []),
    }
