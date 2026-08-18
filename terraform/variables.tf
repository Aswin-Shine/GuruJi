variable "project" {
  description = "Name prefix for every resource and the Project tag."
  type        = string
  default     = "guruji"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.project))
    error_message = "project must be lowercase alphanumeric with hyphens, 2-21 characters, starting with a letter."
  }
}

variable "environment" {
  description = "Deployment environment. Tags resources and prefixes names."
  type        = string
  default     = "pilot"

  validation {
    condition     = contains(["pilot", "staging", "production"], var.environment)
    error_message = "environment must be one of: pilot, staging, production."
  }
}

variable "aws_region" {
  description = "Region to deploy into. ap-south-1 keeps latency low for Tier-2/3 India."
  type        = string
  default     = "ap-south-1"
}

variable "instance_type" {
  description = <<-EOT
    EC2 instance type. The stack needs ~2.2 GB at rest (api 512M + db 1G +
    web 96M + caddy 128M, plus OS and dockerd), so anything with 2 GB or less
    cannot run it. Corpus ingestion is done off-box (see docs/CORPUS.md), so the
    3.6 GB ingestion peak is not a sizing input.

    Free-tier eligible for accounts created on or after 2025-07-15:
      c7i-flex.large  2 vCPU /  4 GB  — fits with headroom
      m7i-flex.large  2 vCPU /  8 GB  — more than this workload can use
  EOT
  type        = string
  default     = "c7i-flex.large"

  validation {
    condition = contains([
      "c7i-flex.large", "m7i-flex.large", "c7i-flex.xlarge", "m7i-flex.xlarge",
      "t3.medium", "t3.large", "t4g.medium", "t4g.large",
    ], var.instance_type)
    error_message = "instance_type must have at least 4 GB of memory; t3.micro/small and t4g.micro/small cannot run this stack."
  }
}

variable "root_volume_size" {
  description = "Root EBS volume in GB. 30 GB is the free-tier ceiling and holds images, pgdata and a corpus dump comfortably."
  type        = number
  default     = 30

  validation {
    condition     = var.root_volume_size >= 20 && var.root_volume_size <= 100
    error_message = "root_volume_size must be between 20 and 100 GB."
  }
}

variable "subnet_id" {
  description = "Subnet to launch into. Leave null to pick a default-VPC subnet automatically."
  type        = string
  default     = null
}

variable "allowed_http_cidrs" {
  description = <<-EOT
    CIDRs allowed to reach ports 80 and 443.

    Port 80 must stay open to the internet for Let's Encrypt's HTTP-01 challenge,
    so narrowing this breaks certificate issuance and renewal. Narrow it only if
    you switch Caddy to the DNS-01 challenge.
  EOT
  type        = list(string)
  default     = ["0.0.0.0/0"]

  validation {
    condition     = length(var.allowed_http_cidrs) > 0
    error_message = "allowed_http_cidrs must contain at least one CIDR."
  }
}

variable "extra_tags" {
  description = "Additional tags merged into the defaults on every resource."
  type        = map(string)
  default     = {}
}
