locals { node_names = sort(keys(var.nodes)) }

resource "proxmox_virtual_environment_file" "cloud_config" {
  for_each     = var.nodes
  content_type = "snippets"
  datastore_id = var.snippets_datastore_id
  node_name    = each.value.proxmox_node
  source_raw {
    data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
      hostname       = each.key
      username       = var.cloud_init_username
      ssh_public_key = trimspace(var.ssh_public_key)
    })
    file_name = "${each.key}-cloud-init.yaml"
  }
}

resource "proxmox_virtual_environment_vm" "k3s" {
  for_each    = var.nodes
  name        = each.key
  description = "Agentic platform K3s node; managed by Terraform"
  tags        = ["agentic-platform", "k3s", "terraform"]
  node_name   = each.value.proxmox_node
  vm_id       = each.value.vm_id
  machine     = "q35"

  started         = true
  on_boot         = true
  protection      = var.protect_vms
  stop_on_destroy = true

  clone {
    vm_id        = var.template_vm_id
    node_name    = var.template_node_name
    datastore_id = var.vm_datastore_id
    full         = true
    retries      = 3
  }
  agent { enabled = true }
  cpu {
    cores = var.vm_cpu_cores
    type  = "host"
  }
  memory { dedicated = var.vm_memory_mb }
  disk {
    datastore_id = var.vm_datastore_id
    interface    = "scsi0"
    size         = var.vm_disk_size_gb
    discard      = "on"
    iothread     = true
    ssd          = true
  }
  initialization {
    datastore_id      = var.vm_datastore_id
    user_data_file_id = proxmox_virtual_environment_file.cloud_config[each.key].id
    dns { servers = var.dns_servers }
    ip_config {
      ipv4 {
        address = each.value.ipv4_address
        gateway = var.ipv4_gateway
      }
    }
  }
  network_device {
    bridge  = var.network_bridge
    model   = "virtio"
    vlan_id = var.network_vlan_id
  }
  dynamic "hostpci" {
    for_each = each.value.gpu_mapping == null ? [] : [each.value.gpu_mapping]
    content {
      device  = "hostpci0"
      mapping = hostpci.value
      pcie    = true
    }
  }
  operating_system { type = "l26" }
  serial_device {}
  startup {
    order      = tostring(index(local.node_names, each.key) + 1)
    up_delay   = "30"
    down_delay = "30"
  }
}
