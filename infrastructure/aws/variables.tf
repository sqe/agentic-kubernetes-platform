variable "name" {
  description = "Cluster and resource name prefix."
  type        = string
  default     = "agentic-platform"
}

variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-west-2"
}

variable "kubernetes_version" {
  description = "EKS Kubernetes minor version supported in the selected region."
  type        = string
  default     = "1.34"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "availability_zones" {
  description = "Three availability zones for the VPC."
  type        = list(string)
  default     = ["us-west-2a", "us-west-2b", "us-west-2c"]
}

variable "artifact_bucket_name" {
  description = "Globally unique S3 bucket; null derives one from account ID and name."
  type        = string
  default     = null
}

variable "gpu_instance_types" {
  description = "GPU node types. Keep gpu_memory_class consistent with every type."
  type        = list(string)
  default     = ["g5.xlarge"]
}

variable "gpu_memory_class" {
  description = "Scheduling label describing usable VRAM class."
  type        = string
  default     = "24gb"
}

variable "auth_callback_urls" {
  description = "Exact OIDC callbacks for the graph UI. HTTPS is required except localhost."
  type        = list(string)
  default     = ["http://localhost:8200/"]
}

variable "auth_logout_urls" {
  description = "Exact post-logout URLs for the graph UI."
  type        = list(string)
  default     = ["http://localhost:8200/"]
}

variable "cognito_domain_prefix" {
  description = "Globally unique Cognito managed-login prefix; null derives one from name and account."
  type        = string
  default     = null
}

variable "tags" {
  type = map(string)
  default = {
    Project   = "agentic-kubernetes-platform"
    ManagedBy = "Terraform"
  }
}
