output "node_addresses" {
  value = { for name, node in var.nodes : name => split("/", node.ipv4_address)[0] }
}
output "gpu_nodes" {
  value = { for name, node in var.nodes : name => node.gpu_memory_class if node.gpu_mapping != null }
}
output "primary_node" {
  value = { name = local.node_names[0], address = split("/", var.nodes[local.node_names[0]].ipv4_address)[0] }
}
output "ssh_username" { value = var.cloud_init_username }
