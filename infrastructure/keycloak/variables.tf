variable "keycloak_url" {
  description = "Keycloak base URL reachable while Terraform runs."
  type        = string
  default     = "http://127.0.0.1:8080"
}

variable "public_keycloak_url" {
  description = "Browser-facing Keycloak URL, without a trailing slash."
  type        = string
}

variable "keycloak_admin_username" {
  type    = string
  default = "admin"
}

variable "keycloak_admin_password" {
  type      = string
  sensitive = true
}

variable "realm_name" {
  type    = string
  default = "agentic-platform"
}

variable "client_id" {
  type    = string
  default = "knowledge-graph-ui"
}

variable "valid_redirect_uris" {
  type    = list(string)
  default = ["http://localhost:8200/"]
}

variable "web_origins" {
  type    = list(string)
  default = ["http://localhost:8200"]
}
