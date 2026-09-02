resource "keycloak_realm" "platform" {
  realm                          = var.realm_name
  display_name                   = "Agentic Kubernetes Platform"
  enabled                        = true
  registration_allowed           = true
  registration_email_as_username = true
  login_with_email_allowed       = true
  duplicate_emails_allowed       = false
  verify_email                   = true
  reset_password_allowed         = true
  remember_me                    = true
  ssl_required                   = "external"

  password_policy = "length(12) and upperCase(1) and lowerCase(1) and digits(1) and specialChars(1) and notUsername"
}

resource "keycloak_openid_client" "graph_ui" {
  realm_id                     = keycloak_realm.platform.id
  client_id                    = var.client_id
  name                         = "Knowledge Graph UI"
  enabled                      = true
  access_type                  = "PUBLIC"
  standard_flow_enabled        = true
  direct_access_grants_enabled = false
  service_accounts_enabled     = false
  valid_redirect_uris          = var.valid_redirect_uris
  web_origins                  = var.web_origins
  pkce_code_challenge_method   = "S256"
  full_scope_allowed           = false
}

resource "keycloak_role" "graph_reader" {
  realm_id    = keycloak_realm.platform.id
  name        = "graph-reader"
  description = "Search and traverse knowledge graphs"
}

resource "keycloak_role" "graph_ingestor" {
  realm_id    = keycloak_realm.platform.id
  name        = "graph-ingestor"
  description = "Upload documents and queue graph extraction"
}
