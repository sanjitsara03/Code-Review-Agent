#AWS Secrets Manager client for loading runtime credentials.


import json
import os

import boto3

#Load credentials from AWS Secrets Manager.
def load_secrets() -> dict:
    
    
    secret_name = os.getenv("SECRETS_NAME")
    if not secret_name:
        raise ValueError("SECRETS_NAME environment variable not set.")

    region = os.getenv("AWS_REGION", "us-east-2")
    client = boto3.client("secretsmanager", region_name=region)

    response = client.get_secret_value(SecretId=secret_name)
    secret = json.loads(response["SecretString"])

    required = {"anthropic_api_key", "github_token", "webhook_secret"}
    missing = required - secret.keys()
    if missing:
        raise ValueError(f"Secret is missing required keys: {missing}")

    return secret
