"""Call the IAM-protected API using the normal AWS credential chain."""

import argparse
import json

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("url", help="API Gateway endpoint including /api/... path")
parser.add_argument("--method", default="GET", choices=["GET", "POST"])
parser.add_argument("--body", help="JSON request body")
parser.add_argument("--region", default="eu-west-1")
args = parser.parse_args()
session = boto3.Session(region_name=args.region)
credentials = session.get_credentials()
if not credentials:
    raise SystemExit("Configure an AWS CLI/SSO profile before calling the API")
body = json.dumps(json.loads(args.body)) if args.body else None
request = AWSRequest(
    method=args.method, url=args.url, data=body, headers={"Content-Type": "application/json"}
)
SigV4Auth(credentials.get_frozen_credentials(), "execute-api", args.region).add_auth(request)
response = httpx.request(
    args.method, args.url, content=body, headers=dict(request.headers), timeout=30
)
print(response.text)
response.raise_for_status()
