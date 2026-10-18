resource "aws_vpc" "pipeline" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.tags, { Name = "${var.name_prefix}-private" })
}

# One private subnet is enough for this single-region demonstration. There is no
# internet gateway, public IP, or NAT gateway. AWS workers use service endpoints.
resource "aws_subnet" "workers" {
  vpc_id                  = aws_vpc.pipeline.id
  cidr_block              = "10.42.1.0/24"
  availability_zone       = var.availability_zone
  map_public_ip_on_launch = false
  tags                    = merge(local.tags, { Name = "${var.name_prefix}-workers" })
}

resource "aws_route_table" "workers" {
  vpc_id = aws_vpc.pipeline.id
  tags   = local.tags
}

resource "aws_route_table_association" "workers" {
  subnet_id      = aws_subnet.workers.id
  route_table_id = aws_route_table.workers.id
}

resource "aws_security_group" "workers" {
  name        = "${var.name_prefix}-workers"
  description = "No inbound access; workers initiate TLS to private AWS service endpoints"
  vpc_id      = aws_vpc.pipeline.id
  egress {
    description = "TLS; subnet has no internet route"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = local.tags
}

resource "aws_security_group" "endpoints" {
  count       = var.is_local ? 0 : 1
  name        = "${var.name_prefix}-endpoints"
  description = "Private AWS endpoints accept TLS only from workers"
  vpc_id      = aws_vpc.pipeline.id
  ingress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.workers.id]
  }
  tags = local.tags
}

resource "aws_vpc_endpoint" "gateway" {
  for_each          = var.is_local ? toset([]) : toset(["s3", "dynamodb"])
  vpc_id            = aws_vpc.pipeline.id
  service_name      = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.workers.id]
  tags              = local.tags
}

# Fargate needs ECR API + registry, S3 image layers, and CloudWatch Logs. The worker
# publishes custom metrics through the monitoring endpoint.
resource "aws_vpc_endpoint" "interface" {
  for_each            = var.is_local ? toset([]) : toset(["ecr.api", "ecr.dkr", "logs", "monitoring"])
  vpc_id              = aws_vpc.pipeline.id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = [aws_subnet.workers.id]
  security_group_ids  = [aws_security_group.endpoints[0].id]
  tags                = local.tags
}
