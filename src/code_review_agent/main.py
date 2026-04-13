import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from code_review_agent.agent import run_review
from code_review_agent.github_client import GitHubClient

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
            key=lambda c: (
                SEVERITY_ORDER.index(c["severity"])
                if c["severity"] in SEVERITY_ORDER
                else 99,
                c["file_path"],
                c["line_number"],
            ),
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


def _parse_pr_ref(pr_ref: str) -> tuple[str, str, int]:
    """Parse owner/repo#pr_number into components.

    Args:
        pr_ref: String like "anthropics/claude-code#123"

    Returns:
        (owner, repo, pr_number)

    Raises:
        ValueError: If format is invalid.
    """
    if "#" not in pr_ref:
        raise ValueError(f"Invalid PR reference: {pr_ref}. Expected format: owner/repo#number")

    repo_part, number_part = pr_ref.rsplit("#", 1)
    if "/" not in repo_part:
        raise ValueError(f"Invalid PR reference: {pr_ref}. Expected format: owner/repo#number")

    owner, repo = repo_part.split("/", 1)
    try:
        pr_number = int(number_part)
    except ValueError:
        raise ValueError(f"Invalid PR number: {number_part}. Expected integer.")

    return owner, repo, pr_number


def _clone_github_repo(clone_url: str, head_sha: str, temp_dir: str) -> str:
    """Clone a GitHub repo and check out to the PR's head commit.

    Args:
        clone_url: Git clone URL (https:// or git://)
        head_sha: Commit SHA to check out
        temp_dir: Temporary directory to clone into

    Returns:
        Path to cloned repo

    Raises:
        subprocess.CalledProcessError: If clone or checkout fails.
    """
    try:
        # Sparse checkout: skip large binary dirs (data/, model_output/, etc.)
        subprocess.run(
            ["git", "clone", "--depth=1", "--filter=blob:limit=500k",
             "--sparse", clone_url, temp_dir],
            check=True,
            capture_output=True,
            timeout=120,
        )
        # Enable sparse checkout patterns — include code, exclude large asset dirs
        subprocess.run(
            ["git", "-C", temp_dir, "sparse-checkout", "set",
             "--no-cone", "*.py", "*.toml", "*.txt", "*.md", "*.json", "*.yaml", "*.yml",
             ":(exclude)data/", ":(exclude)model_output/", ":(exclude)eval_output/",
             ":(exclude)checkpoints/", ":(exclude)sample_docs/"],
            check=True,
            capture_output=True,
            timeout=60,
        )
        subprocess.run(
            ["git", "-C", temp_dir, "checkout", head_sha],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return temp_dir
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to clone/checkout repo: {e.stderr.decode()}") from e


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Autonomous code review agent — local and GitHub modes"
    )

    # Positional args (mutually exclusive with --github)
    parser.add_argument(
        "repo_path",
        nargs="?",
        help="Absolute path to local git repository (local mode)",
    )
    parser.add_argument(
        "diff_file",
        nargs="?",
        help="Path to unified diff file (local mode)",
    )

    # GitHub mode flag
    parser.add_argument(
        "--github",
        help="Review a GitHub PR (format: owner/repo#number, e.g. anthropics/claude-code#123)",
    )
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Review thread ID (default: derived from PR or diff filename)",
    )
    parser.add_argument(
        "--post",
        action="store_true",
        help="Post review back to GitHub (GitHub mode only)",
    )

    args = parser.parse_args()

    # Validate mode selection
    if args.github and (args.repo_path or args.diff_file):
        print("ERROR: Cannot specify both --github and positional arguments.", file=sys.stderr)
        sys.exit(1)

    if args.github:
        # GitHub mode
        try:
            owner, repo, pr_number = _parse_pr_ref(args.github)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)

        try:
            client = GitHubClient()
            print(f"Fetching PR {owner}/{repo}#{pr_number}...")
            metadata = client.get_pr_metadata(owner, repo, pr_number)
            diff_content = client.get_pr_diff(owner, repo, pr_number)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)

        # Clone the repo to the PR's head commit
        temp_dir = tempfile.mkdtemp(prefix="code-review-")
        print(f"Cloning {metadata.repo_url} to {temp_dir}...")
        try:
            repo_path = _clone_github_repo(metadata.repo_url, metadata.head_sha, temp_dir)
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            shutil.rmtree(temp_dir, ignore_errors=True)
            sys.exit(1)

        thread_id = args.thread_id or f"github-{owner}-{repo}-{pr_number}"
        print(f"Starting review — PR: {owner}/{repo}#{pr_number}, thread: {thread_id}")

        result = run_review(
            repo_path=repo_path,
            diff_content=diff_content,
            thread_id=thread_id,
        )

        print_review(result)

        # Post review back to GitHub if requested
        if args.post:
            print("\nPosting review to GitHub...")
            try:
                review_url = client.post_review(
                    owner=owner,
                    repo=repo,
                    pr_number=pr_number,
                    overall_comment=result["final_comment"] or "",
                    approval_status=result["final_status"] or "comment",
                    inline_comments=result.get("review_comments"),
                )
                print(f"Review posted: {review_url}")
            except ValueError as e:
                print(f"ERROR: Failed to post review: {e}", file=sys.stderr)
                sys.exit(1)
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

    else:
        # Local mode
        if not args.repo_path or not args.diff_file:
            print(
                "ERROR: Local mode requires both repo_path and diff_file arguments.",
                file=sys.stderr,
            )
            parser.print_help()
            sys.exit(1)

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
