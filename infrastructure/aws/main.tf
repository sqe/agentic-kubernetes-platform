data "aws_caller_identity" "current" {}

locals {
  azs             = var.availability_zones
  artifact_bucket = coalesce(var.artifact_bucket_name, "${var.name}-${data.aws_caller_identity.current.account_id}-artifacts")
  cluster_tags = {
    "kubernetes.io/cluster/${var.name}" = "shared"
  }
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 6.0"

  name = var.name
  cidr = var.vpc_cidr
  azs  = local.azs

  public_subnets  = [for index, _ in local.azs : cidrsubnet(var.vpc_cidr, 4, index)]
  private_subnets = [for index, _ in local.azs : cidrsubnet(var.vpc_cidr, 4, index + 3)]

  enable_nat_gateway      = true
  single_nat_gateway      = true
  enable_dns_hostnames    = true
  public_subnet_tags      = merge(local.cluster_tags, { "kubernetes.io/role/elb" = "1" })
  private_subnet_tags     = merge(local.cluster_tags, { "kubernetes.io/role/internal-elb" = "1" })
  map_public_ip_on_launch = false
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.0"

  name                   = var.name
  kubernetes_version     = var.kubernetes_version
  endpoint_public_access = true
  enable_irsa            = true

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  # Cilium supplies CNI, kube-proxy replacement, policy, and Gateway API.
  addons = {
    coredns                = { most_recent = true }
    aws-ebs-csi-driver     = { most_recent = true }
    eks-pod-identity-agent = { most_recent = true }
  }

  enable_cluster_creator_admin_permissions = true

  eks_managed_node_groups = {
    system = {
      instance_types = ["m7i.large"]
      min_size       = 2
      desired_size   = 2
      max_size       = 6
      ami_type       = "AL2023_x86_64_STANDARD"
      labels         = { workload = "system" }
      iam_role_additional_policies = {
        cilium_eni = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
      }
      tags = {
        "k8s.io/cluster-autoscaler/enabled"     = "true"
        "k8s.io/cluster-autoscaler/${var.name}" = "owned"
      }
    }
    gpu = {
      instance_types = var.gpu_instance_types
      min_size       = 0
      desired_size   = 0
      max_size       = 20
      ami_type       = "AL2023_x86_64_NVIDIA"
      labels = {
        accelerator      = "nvidia"
        gpu-memory-class = var.gpu_memory_class
        workload         = "knowledge-extraction"
      }
      taints = {
        gpu = { key = "nvidia.com/gpu", value = "true", effect = "NO_SCHEDULE" }
      }
      iam_role_additional_policies = {
        cilium_eni = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
      }
      tags = {
        "k8s.io/cluster-autoscaler/enabled"                              = "true"
        "k8s.io/cluster-autoscaler/${var.name}"                          = "owned"
        "k8s.io/cluster-autoscaler/node-template/label/accelerator"      = "nvidia"
        "k8s.io/cluster-autoscaler/node-template/label/gpu-memory-class" = var.gpu_memory_class
        "k8s.io/cluster-autoscaler/node-template/taint/nvidia.com/gpu"   = "true:NoSchedule"
      }
    }
  }
}

resource "aws_s3_bucket" "artifacts" {
  bucket        = local.artifact_bucket
  force_destroy = false
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "abort-multipart"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
    noncurrent_version_expiration { noncurrent_days = 30 }
  }
}

resource "aws_ecr_repository" "images" {
  for_each             = toset(["runtime", "model-store", "inference", "training"])
  name                 = "${var.name}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
}

data "aws_iam_policy_document" "model_store_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [module.eks.oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${replace(module.eks.cluster_oidc_issuer_url, "https://", "")}:sub"
      values   = ["system:serviceaccount:agentic-platform:agentic-platform"]
    }
  }
}

resource "aws_iam_role" "model_store" {
  name               = "${var.name}-model-store"
  assume_role_policy = data.aws_iam_policy_document.model_store_assume.json
}

data "aws_iam_policy_document" "model_store" {
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.artifacts.arn]
  }
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
    resources = ["${aws_s3_bucket.artifacts.arn}/*"]
  }
}

resource "aws_iam_role_policy" "model_store" {
  role   = aws_iam_role.model_store.id
  policy = data.aws_iam_policy_document.model_store.json
}

resource "aws_cognito_user_pool" "users" {
  name                     = "${var.name}-users"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "OPTIONAL"

  software_token_mfa_configuration {
    enabled = true
  }

  username_configuration {
    case_sensitive = false
  }

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    require_uppercase                = true
    temporary_password_validity_days = 7
  }

  sign_in_policy {
    allowed_first_auth_factors = ["PASSWORD"]
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  verification_message_template {
    default_email_option = "CONFIRM_WITH_CODE"
  }

  schema {
    attribute_data_type = "String"
    mutable             = true
    name                = "email"
    required            = true
    string_attribute_constraints {
      min_length = 5
      max_length = 320
    }
  }
}

resource "aws_cognito_user_pool_client" "graph_ui" {
  name         = "${var.name}-graph-ui"
  user_pool_id = aws_cognito_user_pool.users.id

  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = var.auth_callback_urls
  logout_urls                          = var.auth_logout_urls
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  access_token_validity                = 60
  id_token_validity                    = 60
  refresh_token_validity               = 30

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
}

resource "aws_cognito_user_pool_domain" "graph_ui" {
  domain = coalesce(
    var.cognito_domain_prefix,
    substr("${var.name}-${data.aws_caller_identity.current.account_id}", 0, 63)
  )
  user_pool_id = aws_cognito_user_pool.users.id
}
