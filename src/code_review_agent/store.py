import os
from datetime import datetime, timezone

import boto3


def save_review(
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    status: str,
    comment_count: int,
    duration_ms: int,
    region: str = "us-east-2",
) -> None:
    #Save a completed review record to DynamoDB.

    
    table_name = os.getenv("DYNAMODB_TABLE", "code-review-history")
    client = boto3.resource("dynamodb", region_name=region)
    table = client.Table(table_name)

    table.put_item(Item={
        "pr_id": f"{owner}/{repo}#{pr_number}",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "owner": owner,
        "repo": repo,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "status": status,
        "comment_count": comment_count,
        "duration_ms": duration_ms,
    })
