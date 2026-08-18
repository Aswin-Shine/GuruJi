locals {
  name_prefix = "${var.project}-${var.environment}"

  common_tags = merge({
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }, var.extra_tags)
}

# ---------------------------------------------------------------------------
#  Network. The default VPC is used deliberately: a purpose-built VPC for a
#  single public instance adds subnets, route tables and an internet gateway
#  that would carry no configuration this deployment actually differs on.
#  Pass subnet_id to override.
# ---------------------------------------------------------------------------

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# ---------------------------------------------------------------------------
#  AMI. Amazon Linux 2023, resolved at plan time.
#
#  `owners` is mandatory, not decorative: AWS provider v6 errors on a
#  most_recent lookup without an owner or image-id filter, because a name
#  pattern alone can be satisfied by an AMI published by anyone.
#
#  The architecture filter is derived from the instance type so a t4g (Graviton)
#  choice cannot silently pair with an x86 image and fail at launch.
# ---------------------------------------------------------------------------

locals {
  cpu_architecture = startswith(var.instance_type, "t4g.") ? "arm64" : "x86_64"
}

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-kernel-6.1-${local.cpu_architecture}"]
  }

  filter {
    name   = "state"
    values = ["available"]
  }
}

# ---------------------------------------------------------------------------
#  Security group. Two inbound ports, nothing else.
#
#  No SSH rule. Access is via SSM Session Manager (IAM role below), which needs
#  no inbound port at all — the agent dials out. An always-open port 22 is the
#  single most-scanned surface on any public instance, and removing it removes
#  the key-management problem with it.
# ---------------------------------------------------------------------------

resource "aws_security_group" "web" {
  name        = "${local.name_prefix}-web"
  description = "HTTP and HTTPS ingress for the Caddy TLS terminator"
  vpc_id      = data.aws_vpc.default.id

  tags = { Name = "${local.name_prefix}-web" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "http" {
  for_each = toset(var.allowed_http_cidrs)

  security_group_id = aws_security_group.web.id
  description       = "HTTP: ACME HTTP-01 challenge and the redirect to HTTPS"
  cidr_ipv4         = each.value
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "https" {
  for_each = toset(var.allowed_http_cidrs)

  security_group_id = aws_security_group.web.id
  description       = "HTTPS"
  cidr_ipv4         = each.value
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "https_quic" {
  for_each = toset(var.allowed_http_cidrs)

  security_group_id = aws_security_group.web.id
  description       = "HTTP/3 over QUIC, which Caddy serves on 443/udp"
  cidr_ipv4         = each.value
  from_port         = 443
  to_port           = 443
  ip_protocol       = "udp"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.web.id
  description       = "Outbound: OpenAI, Let's Encrypt, SSM, package and image registries"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# ---------------------------------------------------------------------------
#  Instance role — SSM Session Manager only.
#
#  This is what replaces an SSH key pair. No key to lose, no key to rotate, no
#  inbound port, and every session is logged in CloudTrail against a named IAM
#  principal rather than against "whoever holds the .pem".
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "${local.name_prefix}-instance"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "instance" {
  name = "${local.name_prefix}-instance"
  role = aws_iam_role.instance.name
}

# ---------------------------------------------------------------------------
#  The instance.
# ---------------------------------------------------------------------------

resource "aws_instance" "app" {
  ami           = data.aws_ami.al2023.id
  instance_type = var.instance_type
  subnet_id     = coalesce(var.subnet_id, data.aws_subnets.default.ids[0])

  vpc_security_group_ids = [aws_security_group.web.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name

  # The Elastic IP below is the address that matters. A stop/start would hand
  # out a new auto-assigned address and the hostname would point at nothing, so
  # this stays off to keep exactly one public address associated.
  associate_public_ip_address = false

  user_data                   = file("${path.module}/user_data.sh")
  user_data_replace_on_change = false

  root_block_device {
    volume_size           = var.root_volume_size
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
    tags                  = { Name = "${local.name_prefix}-root" }
  }

  # IMDSv2 required. With the v1 default, any server-side request forgery in the
  # app can read instance credentials from the metadata endpoint; requiring a
  # session token closes that whole class.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2 # containers reach the metadata service through one extra hop
    instance_metadata_tags      = "enabled"
  }

  monitoring = false # detailed monitoring is billed; the 5-minute default is enough here

  tags = { Name = "${local.name_prefix}-app" }

  lifecycle {
    ignore_changes = [ami]
  }
}

# ---------------------------------------------------------------------------
#  Elastic IP
# ---------------------------------------------------------------------------

resource "aws_eip" "app" {
  domain = "vpc"

  tags = { Name = "${local.name_prefix}-eip" }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_eip_association" "app" {
  instance_id   = aws_instance.app.id
  allocation_id = aws_eip.app.id
}
