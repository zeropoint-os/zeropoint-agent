# Test module for zeropoint-agent.
#
# Smallest possible module that exercises the full install contract:
# system input variables, a user variable with a default, a docker
# container, and the required `main` + `main_ports` outputs plus an
# extra output to test the from_output VarNode pipeline.

terraform {
  required_providers {
    docker = {
      source  = "kreuzwerker/docker"
      version = "~> 3.0"
    }
  }
}

# ---- system vars (injected by zeropoint) -----------------------------------

variable "zp_module_id" {
  type        = string
  description = "Unique identifier for this module instance (injected by zeropoint)."
}

variable "zp_network_name" {
  type        = string
  description = "Pre-created docker network for this module (injected by zeropoint)."
}

variable "zp_arch" {
  type        = string
  default     = "amd64"
  description = "Target CPU arch (injected by zeropoint)."
}

variable "zp_gpu_vendor" {
  type        = string
  default     = ""
  description = "GPU vendor (injected by zeropoint)."
}

variable "zp_module_storage" {
  type        = string
  description = "Host path for the module's persistent storage (injected by zeropoint)."
}

# ---- user vars -------------------------------------------------------------

variable "greeting" {
  type        = string
  default     = "hello from zeropoint test module"
  description = "Message echoed by the container on startup."
}

variable "sleep_seconds" {
  type        = number
  default     = 86400
  description = "How long the container stays alive after greeting."
}

# ---- resources -------------------------------------------------------------

resource "docker_image" "alpine" {
  name         = "alpine:3.19"
  keep_locally = true
}

resource "docker_container" "main" {
  name    = "${var.zp_module_id}-main"
  image   = docker_image.alpine.image_id
  command = ["sh", "-c", "echo '${var.greeting}'; sleep ${var.sleep_seconds}"]

  networks_advanced {
    name = var.zp_network_name
  }

  restart = "unless-stopped"
}

# ---- outputs ---------------------------------------------------------------

# Required by the zeropoint contract: the primary container resource.
output "main" {
  value       = docker_container.main
  description = "Main alpine container."
}

# Required: ports declared by the main container. The test module has no
# real listener; we declare a placeholder to satisfy the contract.
output "main_ports" {
  value = {
    placeholder = {
      port        = 0
      protocol    = "tcp"
      transport   = "tcp"
      description = "Placeholder (test module has no real listener)"
      default     = true
    }
  }
  description = "Service ports for the main container."
}

# Extra outputs to exercise the from_output VarNode pipeline.
output "greeting_echoed" {
  value       = var.greeting
  description = "Echoes the configured greeting; verifies user var → output flow."
}

output "container_name" {
  value       = docker_container.main.name
  description = "The actual container name resolved by docker."
}
