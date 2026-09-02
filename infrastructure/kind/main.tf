locals {
  repository_root = abspath("${path.module}/../..")
  config_path     = "${path.module}/.generated-${var.cluster_name}.yaml"
  kube_context    = "kind-${var.cluster_name}"
  chart_files     = sort(tolist(fileset("${path.module}/../../deploy/helm/agentic-platform", "**")))
  chart_hash      = sha256(join("", [for file in local.chart_files : filesha256("${local.repository_root}/deploy/helm/agentic-platform/${file}")]))
  runtime_hash = sha256(join("", concat(
    [for file in sort(tolist(fileset("${path.module}/../../src", "**"))) : filesha256("${local.repository_root}/src/${file}") if !strcontains(file, "__pycache__")],
    [for file in sort(tolist(fileset("${path.module}/../../agents", "**"))) : filesha256("${local.repository_root}/agents/${file}") if !strcontains(file, "__pycache__")],
    [for file in sort(tolist(fileset("${path.module}/../../services", "**"))) : filesha256("${local.repository_root}/services/${file}") if !strcontains(file, "__pycache__")],
  )))
}

resource "local_file" "kind_config" {
  filename = local.config_path
  content = templatefile("${path.module}/kind.yaml.tftpl", {
    cluster_name = var.cluster_name
    node_image   = var.node_image
    worker_count = var.worker_count
    http_port    = var.http_port
    https_port   = var.https_port
  })
}

resource "terraform_data" "cluster" {
  input = {
    cluster_name = var.cluster_name
    config_path  = local_file.kind_config.filename
  }
  triggers_replace = [local_file.kind_config.content_sha256]

  provisioner "local-exec" {
    command = "bash '${path.module}/scripts/create-cluster.sh' '${var.cluster_name}' '${local_file.kind_config.filename}'"
  }

  provisioner "local-exec" {
    when       = destroy
    command    = "kind delete cluster --name '${self.input.cluster_name}'"
    on_failure = continue
  }
}

resource "terraform_data" "platform" {
  input = { cluster_name = var.cluster_name }
  triggers_replace = [
    var.cilium_version,
    var.gateway_api_version,
    var.keda_version,
    var.keycloak_operator_version,
    var.runtime_image,
    var.build_runtime_image,
    local.chart_hash,
    local.runtime_hash,
    filesha256("${path.module}/scripts/bootstrap.sh"),
    filesha256("${local.repository_root}/deploy/cilium/values-kind.yaml"),
    filesha256("${local.repository_root}/images/runtime/Dockerfile"),
  ]

  provisioner "local-exec" {
    command = join(" ", [
      "bash '${path.module}/scripts/bootstrap.sh'",
      "'${var.cluster_name}'",
      "'${var.cilium_version}'",
      "'${var.gateway_api_version}'",
      "'${var.keda_version}'",
      "'${var.keycloak_operator_version}'",
      "'${var.runtime_image}'",
      "'${var.build_runtime_image}'",
      "'${local.repository_root}'",
    ])
  }

  depends_on = [terraform_data.cluster]
}
