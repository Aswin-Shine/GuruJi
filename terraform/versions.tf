terraform {
  # 1.10 is the floor for S3 native state locking (use_lockfile in backend.tf).
  # Below that you need a DynamoDB table, which is an extra resource to create,
  # pay for and bootstrap before Terraform can run at all.
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # Pinned to the 6.x line. v6 removed the `vpc` argument on aws_eip and made
      # an owner filter mandatory on aws_ami data sources; this configuration is
      # written for that behaviour and will not plan cleanly against 5.x.
      version = "~> 6.0"
    }
  }
}
