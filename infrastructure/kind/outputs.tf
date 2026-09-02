output "kube_context" { value = local.kube_context }
output "gateway_url" { value = "http://127.0.0.1:${var.http_port}" }
output "verify_command" {
  value = "kubectl --context ${local.kube_context} get pods,gateway,httproute -A"
}
