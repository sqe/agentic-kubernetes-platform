variable "cluster_name" {
  type    = string
  default = "agentic-platform"
}

variable "node_image" {
  type    = string
  default = "kindest/node:v1.34.0"
}

variable "worker_count" {
  type    = number
  default = 1
  validation {
    condition     = var.worker_count >= 1 && var.worker_count <= 4
    error_message = "worker_count must be between 1 and 4."
  }
}

variable "http_port" {
  type    = number
  default = 8080
}

variable "https_port" {
  type    = number
  default = 8443
}

variable "cilium_version" {
  type    = string
  default = "1.20.1"
}

variable "gateway_api_version" {
  type    = string
  default = "1.5.1"
}

variable "keda_version" {
  type    = string
  default = "2.17.2"
}

variable "keycloak_operator_version" {
  type    = string
  default = "26.3.2"
}

variable "runtime_image" {
  type    = string
  default = "agentic-platform-runtime:dev"
}

variable "build_runtime_image" {
  type    = bool
  default = true
}
