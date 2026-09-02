output "cluster_name" { value = module.eks.cluster_name }
output "cluster_endpoint" { value = module.eks.cluster_endpoint }
output "artifact_bucket" { value = aws_s3_bucket.artifacts.id }
output "model_store_role_arn" { value = aws_iam_role.model_store.arn }
output "ecr_repositories" { value = { for name, repository in aws_ecr_repository.images : name => repository.repository_url } }
output "configure_kubectl" { value = "aws eks update-kubeconfig --region ${var.region} --name ${module.eks.cluster_name}" }
output "auth" {
  value = {
    provider               = "cognito"
    client_id              = aws_cognito_user_pool_client.graph_ui.id
    issuer                 = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.users.id}"
    jwks_url               = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.users.id}/.well-known/jwks.json"
    authorization_endpoint = "https://${aws_cognito_user_pool_domain.graph_ui.domain}.auth.${var.region}.amazoncognito.com/oauth2/authorize"
    token_endpoint         = "https://${aws_cognito_user_pool_domain.graph_ui.domain}.auth.${var.region}.amazoncognito.com/oauth2/token"
    registration_endpoint  = "https://${aws_cognito_user_pool_domain.graph_ui.domain}.auth.${var.region}.amazoncognito.com/signup"
    logout_endpoint        = "https://${aws_cognito_user_pool_domain.graph_ui.domain}.auth.${var.region}.amazoncognito.com/logout"
  }
}
