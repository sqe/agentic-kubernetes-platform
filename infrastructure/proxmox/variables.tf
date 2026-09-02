variable "proxmox_endpoint" { type = string }
variable "proxmox_api_token" {
  type      = string
  sensitive = true
}
variable "proxmox_insecure" {
  type    = bool
  default = false
}
variable "template_vm_id" { type = number }
variable "template_node_name" { type = string }
variable "ipv4_gateway" { type = string }
variable "ssh_public_key" { type = string }

variable "nodes" {
  description = "K3s server VMs. gpu_mapping names refer to Proxmox cluster PCI resource mappings."
  type = map(object({
    proxmox_node     = string
    vm_id            = number
    ipv4_address     = string
    gpu_mapping      = optional(string)
    gpu_memory_class = optional(string, "24gb")
  }))
  validation {
    condition     = length(var.nodes) >= 3
    error_message = "At least three server nodes are required for HA."
  }
}

variable "dns_servers" {
  type    = list(string)
  default = ["1.1.1.1", "1.0.0.1"]
}
variable "network_bridge" {
  type    = string
  default = "vmbr0"
}
variable "network_vlan_id" {
  type    = number
  default = null
}
variable "vm_datastore_id" {
  type    = string
  default = "local-lvm"
}
variable "snippets_datastore_id" {
  type    = string
  default = "local"
}
variable "vm_cpu_cores" {
  type    = number
  default = 8
}
variable "vm_memory_mb" {
  type    = number
  default = 32768
}
variable "vm_disk_size_gb" {
  type    = number
  default = 200
}
variable "cloud_init_username" {
  type    = string
  default = "ubuntu"
}
variable "protect_vms" {
  type    = bool
  default = true
}
