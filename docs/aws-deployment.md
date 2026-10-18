# Deploying to AWS

The local pipeline is the default environment. This guide describes the separate AWS configuration; it is not an assertion that it has already been applied to a live AWS account.

## Prepare the artifacts

Use Python 3.12, Terraform 1.10+, Docker, and an AWS CLI/SSO profile authorized to create the resources in `infra/modules/pipeline`. Build the Lambda bundle and web console:

```bash
make setup
make package
cd web && npm run build
cd ..
```

Select your intended AWS profile and verify its account with `aws sts get-caller-identity`. Choose a unique lowercase name prefix; it becomes part of globally unique S3 bucket names. The default region is `eu-west-1`, and the default availability zone is `eu-west-1a`; change both together when using another region.

## Plan and create the infrastructure

Create an ignored `infra/environments/aws/terraform.tfvars`:

```hcl
name_prefix = "your-unique-media-name"
region = "eu-west-1"
availability_zone = "eu-west-1a"
worker_image = "123456789012.dkr.ecr.eu-west-1.amazonaws.com/your-unique-media-name-worker:v1"
```

Replace the account number and prefix with your own values. The worker image reference must use the repository created by this configuration. The first apply creates the ECR repository and task definition; push the image before submitting any uploads.

```bash
terraform -chdir=infra/environments/aws init
terraform -chdir=infra/environments/aws plan -out=deployment.tfplan
terraform -chdir=infra/environments/aws apply deployment.tfplan
```

The private subnet has no NAT gateway or public task IPs. ECR API/registry, Logs, and Monitoring interface endpoints plus S3/DynamoDB gateway endpoints supply the required service access. Interface endpoints incur ongoing charges while provisioned, even with no jobs running. Review the plan and remove the environment when finished.

## Push the worker

Read the `worker_repository_url` in `terraform output -json config`. Authenticate Docker to that ECR registry with the standard `aws ecr get-login-password` workflow, then build and push a Linux x86-64 image:

```bash
docker buildx build --platform linux/amd64 \
  -f docker/worker.Dockerfile \
  -t 123456789012.dkr.ecr.eu-west-1.amazonaws.com/your-unique-media-name-worker:v1 \
  --push .
```

Repository tags are immutable. For later releases use a new tag or digest and apply the updated `worker_image` value.

## Access the application

The API Gateway HTTP API requires signed IAM requests. Grant the operator `execute-api:Invoke` scoped to the `api_invoke_arn` Terraform output. The included client uses the normal AWS credential chain:

```bash
.venv/bin/python scripts/aws_request.py \
  https://YOUR_API.execute-api.eu-west-1.amazonaws.com/api/health
```

The React console can also run on your own machine against the AWS services, using the same FastAPI application and your configured AWS profile:

```bash
.venv/bin/python scripts/local_env.py --environment aws
```

Open http://localhost:8000. This runs the API locally using your AWS SDK permissions; it does not make the IAM-protected HTTP API anonymous or store your credentials in the browser. Use the Terraform API role policy as the resource-scoped starting point for the operator's application permissions. Configure the allowed origins if you change the console's host. Reliability scenario injection remains disabled in this mode.

Public web hosting and user identity are intentionally outside this operator-console deployment. A multi-user product needs an authenticated frontend, tenant authorization, tenant-scoped storage queries, and upload quotas.

## Validate on AWS

Start with a small synthetic clip. Verify that unsigned HTTP API requests are rejected, a signed upload completes, all three output assets decode, and the worker runs without a public IP. Inspect the actual CloudWatch logs, execution history, queue redrive settings, and IAM access-denied behaviour. This live validation is separate from the emulator integration evidence committed in this repository.

## Teardown

Stop new uploads and let executions settle. Versioned S3 buckets and ECR intentionally refuse destructive cleanup by default. Explicitly empty the intended buckets, including non-current versions, and the intended ECR repository before destroying the environment. Keep a copy of any results you want to retain.

```bash
terraform -chdir=infra/environments/aws plan -destroy
terraform -chdir=infra/environments/aws destroy
```

Confirm that the interface endpoints and associated resources have been removed in the selected account and region. Store AWS Terraform state in a protected remote backend for shared or long-lived use; the example uses an ignored local state file.
