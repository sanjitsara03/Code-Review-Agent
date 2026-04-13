"""GitHub API client for PR review operations."""
import os
from dataclasses import dataclass

from github import Github, GithubException


@dataclass
class PRMetadata:
    """PR metadata extracted from GitHub."""

    owner: str
    repo: str
    number: int
    title: str
    author: str
    description: str
    base_branch: str
    head_branch: str
    head_sha: str
    repo_url: str


class GitHubClient:
    """GitHub API client for fetching PR data and posting reviews."""

    def __init__(self, token: str | None = None):
        """Initialize GitHub client with personal access token.

        Args:
            token: GitHub personal access token. Defaults to GITHUB_TOKEN env var.
        """
        token = token or os.getenv("GITHUB_TOKEN")
        if not token:
            raise ValueError(
                "GitHub token not provided. Set GITHUB_TOKEN env var or pass token= argument."
            )
        self.github = Github(token)

    def get_pr_metadata(self, owner: str, repo: str, pr_number: int) -> PRMetadata:
        """Fetch PR metadata from GitHub.

        Args:
            owner: Repository owner username.
            repo: Repository name.
            pr_number: PR number.

        Returns:
            PRMetadata object with PR details.

        Raises:
            ValueError: If PR not found or API error.
        """
        try:
            gh_repo = self.github.get_user(owner).get_repo(repo)
            pr = gh_repo.get_pull(pr_number)

            return PRMetadata(
                owner=owner,
                repo=repo,
                number=pr_number,
                title=pr.title,
                author=pr.user.login,
                description=pr.body or "",
                base_branch=pr.base.ref,
                head_branch=pr.head.ref,
                head_sha=pr.head.sha,
                repo_url=gh_repo.clone_url,
            )
        except GithubException as e:
            error_msg = e.data.get("message", str(e))
            raise ValueError(f"Failed to fetch PR {owner}/{repo}#{pr_number}: {error_msg}") from e

    def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """Fetch the unified diff for a PR.

        Args:
            owner: Repository owner username.
            repo: Repository name.
            pr_number: PR number.

        Returns:
            Unified diff as string.

        Raises:
            ValueError: If PR not found or API error.
        """
        try:
            gh_repo = self.github.get_user(owner).get_repo(repo)
            pr = gh_repo.get_pull(pr_number)
            # Get raw diff via the patch URL (follow redirects)
            import httpx

            diff_url = pr.patch_url
            response = httpx.get(diff_url, timeout=10, follow_redirects=True)
            response.raise_for_status()
            return response.text
        except (GithubException, Exception) as e:
            raise ValueError(f"Failed to fetch diff for {owner}/{repo}#{pr_number}: {e}") from e

    def post_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        overall_comment: str,
        approval_status: str,
        inline_comments: list[dict] | None = None,
    ) -> str:
        """Post a review to a GitHub PR.

        Args:
            owner: Repository owner username.
            repo: Repository name.
            pr_number: PR number.
            overall_comment: Overall review comment.
            approval_status: "approve", "request_changes", or "comment".
            inline_comments: List of inline comments with keys:
                - file_path: str
                - line_number: int
                - comment: str
                - severity: str (for context, not used by GitHub API)

        Returns:
            Review URL if successfully posted.

        Raises:
            ValueError: If posting fails.
        """
        status_map = {
            "approve": "APPROVE",
            "request_changes": "REQUEST_CHANGES",
            "comment": "COMMENT",
        }

        gh_status = status_map.get(approval_status)
        if not gh_status:
            raise ValueError(f"Invalid approval_status: {approval_status}")

        try:
            gh_repo = self.github.get_user(owner).get_repo(repo)
            pr = gh_repo.get_pull(pr_number)

            head_commit = gh_repo.get_commit(pr.head.sha)

            # Post overall review first 
            try:
                review = pr.create_review(
                    commit=head_commit,
                    body=overall_comment,
                    event=gh_status,
                )
            except GithubException as e:
                # GitHub won't let you approve/request_changes on your own PR.
                # Fall back to COMMENT event so the review still posts.
                self_review = any(
                    "own pull request" in str(err).lower()
                    for err in e.data.get("errors", [])
                )
                if self_review and gh_status != "COMMENT":
                    review = pr.create_review(
                        commit=head_commit,
                        body=f"*Note: approval status was `{approval_status}` but cannot be set on your own PR.*\n\n{overall_comment}",
                        event="COMMENT",
                    )
                else:
                    raise

            # Post inline comments as separate review comments
            if inline_comments:
                for c in inline_comments:
                    try:
                        pr.create_review_comment(
                            body=f"**[{c['severity'].upper()}]** {c['comment']}",
                            commit=head_commit,
                            path=c["file_path"],
                            line=c["line_number"],
                            side="RIGHT",
                        )
                    except GithubException:
                        # Line may not be in the diff — skip gracefully
                        pass

            return review.html_url
        except GithubException as e:
            error_msg = e.data.get("message", str(e))
            raise ValueError(
                f"Failed to post review to {owner}/{repo}#{pr_number}: {error_msg}"
            ) from e
