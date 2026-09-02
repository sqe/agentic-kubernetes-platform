locals {
  issuer = "${trimsuffix(var.public_keycloak_url, "/")}/realms/${keycloak_realm.platform.realm}"
}

output "auth" {
  value = {
    provider               = "keycloak"
    client_id              = keycloak_openid_client.graph_ui.client_id
    issuer                 = local.issuer
    jwks_url               = "${local.issuer}/protocol/openid-connect/certs"
    authorization_endpoint = "${local.issuer}/protocol/openid-connect/auth"
    token_endpoint         = "${local.issuer}/protocol/openid-connect/token"
    registration_endpoint  = "${local.issuer}/protocol/openid-connect/auth?kc_action=register"
    logout_endpoint        = "${local.issuer}/protocol/openid-connect/logout"
  }
}
