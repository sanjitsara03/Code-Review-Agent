import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from code_review_agent.agent import run_review

load_dotenv()

SEVERITY_ORDER = ["blocker", "issue", "suggestion", "nitpick"]


def print_review(result: dict) -> None:
    status = result["final_status"] or "unknown"
    label = {
        "approve": "APPROVED",
        "request_changes": "CHANGES REQUESTED",
        "comment": "COMMENT",
    }.get(status, status.upper())

    print()
    print("=" * 60)
    print(f"  CODE REVIEW — {label}")
    print("=" * 60)
    print()
    print(result["final_comment"] or "(no overall comment)")
    print()

    comments = result["review_comments"]
    if comments:
        # Sort by severity then file/line
        comments = sorted(
            comments,
            key=lambda c: (SEVERITY_ORDER.index(c["severity"]) if c["severity"] in SEVERITY_ORDER else 99,
                           c["file_path"], c["line_number"]),
        )
        print(f"--- {len(comments)} inline comment(s) ---")
        print()
        for c in comments:
            print(f"  [{c['severity'].upper()}] {c['file_path']}:{c['line_number']}")
            print(f"  {c['comment']}")
            print()
    else:
        print("(no inline comments)")
        print()

    print("=" * 60)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Autonomous code review agent — Stage 1 local mode"
    )
    parser.add_argument("repo_path", help="Absolute path to the local git repository to review")
    parser.add_argument("diff_file", help="Path to a unified diff file (.diff or .patch)")
    parser.add_argument("--thread-id", default=None, help="Review thread ID (default: derived from diff filename)")
    args = parser.parse_args()

    repo_path = str(Path(args.repo_path).resolve())
    diff_path = Path(args.diff_file)

    if not Path(repo_path).is_dir():
        print(f"ERROR: repo_path '{repo_path}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    if not diff_path.is_file():
        print(f"ERROR: diff_file '{args.diff_file}' not found.", file=sys.stderr)
        sys.exit(1)

    diff_content = diff_path.read_text()
    thread_id = args.thread_id or f"review-{diff_path.stem}"

    print(f"Starting review — repo: {repo_path}, diff: {diff_path.name}, thread: {thread_id}")

    result = run_review(
        repo_path=repo_path,
        diff_content=diff_content,
        thread_id=thread_id,
    )

    print_review(result)


if __name__ == "__main__":
    main()
