terraform {
  # Partial configuration — the bucket name is account-specific and does not
  # belong in version control. Supply it at init time:
  #
  #   terraform init -backend-config=backend.hcl
  #
  # Bootstrap the bucket once, before the first init (AWS CLI is enough; a whole
  # second Terraform project to create one bucket is not worth the ceremony):
  #
  #   aws s3api create-bucket --bucket guruji-tfstate-<suffix> \
  #     --region ap-south-1 --create-bucket-configuration LocationConstraint=ap-south-1
  #   aws s3api put-bucket-versioning --bucket guruji-tfstate-<suffix> \
  #     --versioning-configuration Status=Enabled
  #   aws s3api put-public-access-block --bucket guruji-tfstate-<suffix> \
  #     --public-access-block-configuration \
  #     "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
  #
  # Versioning is the important one: state is the only record of what exists, and
  # a corrupted or truncated push is recoverable from a previous version.
  backend "s3" {}
}
