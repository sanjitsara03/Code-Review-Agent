"""Configuration loaded from environment variables."""
import os
from dataclasses import dataclass


@dataclass
class Config:
    """Configuration for code review agent."""

    anthropic_api_key: str
    github_token: str
    aws_region: str = "us-east-1"
    aws_profile: str = "default"
    max_tool_calls: int = 25
    max_tokens_per_review: int = 100_000
    model_tool_calling: str = "claude-haiku-4-5-20251001"
    model_final_review: str = "claude-sonnet-4-6-20250514"

    @classmethod
    def from_env(cls) -> "Config":
        """Load config from environment variables.

        Raises:
            ValueError: If required env vars are missing.
        """
        api_key = os.getenv("ANTHROPIC_API_KEY")
        github_token = os.getenv("GITHUB_TOKEN")

        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable not set")
        if not github_token:
            raise ValueError("GITHUB_TOKEN environment variable not set")

        return cls(
            anthropic_api_key=api_key,
            github_token=github_token,
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
            aws_profile=os.getenv("AWS_PROFILE", "default"),
            max_tool_calls=int(os.getenv("MAX_TOOL_CALLS", "25")),
            max_tokens_per_review=int(os.getenv("MAX_TOKENS_PER_REVIEW", "100000")),
            model_tool_calling=os.getenv(
                "MODEL_TOOL_CALLING", "claude-haiku-4-5-20251001"
            ),
            model_final_review=os.getenv(
                "MODEL_FINAL_REVIEW", "claude-sonnet-4-6-20250514"
            ),
        )
