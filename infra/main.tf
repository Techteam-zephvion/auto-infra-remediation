terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.15"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.32"
    }
  }
  
  # ── Remote Backend (GCS) ────────────────────────────────────────────────────
  # Store Terraform state in Google Cloud Storage for:
  # - Team collaboration (shared state)
  # - State locking (prevent concurrent modifications)
  # - Versioning (state history and rollback)
  # - Security (IAM-controlled access)
  #
  # Setup:
  #   1. Create GCS bucket: gsutil mb gs://${PROJECT_ID}-terraform-state
  #   2. Enable versioning: gsutil versioning set on gs://${PROJECT_ID}-terraform-state
  #   3. Set lifecycle: gsutil lifecycle set lifecycle.json gs://${PROJECT_ID}-terraform-state
  #   4. Migrate state: terraform init -migrate-state
  #
  # Uncomment and configure when ready for production:
  # backend "gcs" {
  #   bucket  = "my-project-terraform-state"
  #   prefix  = "auto-remediation/state"
  # }
}

# ── Variables ─────────────────────────────────────────────────────────────────

variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for the cluster"
  type        = string
  default     = "us-central1"
}

variable "cluster_name" {
  description = "GKE Autopilot cluster name"
  type        = string
  default     = "auto-remediation-cluster"
}

variable "webhook_url" {
  description = "AlertManager webhook URL (public IP or internal service URL of the remediation API)"
  type        = string
  default     = "http://auto-remediation-webhook.default.svc.cluster.local:8001/alert"
}

# ── Providers ─────────────────────────────────────────────────────────────────

provider "google" {
  project = var.project_id
  region  = var.region
}

data "google_client_config" "default" {}

provider "kubernetes" {
  host                   = "https://${google_container_cluster.autopilot.endpoint}"
  token                  = data.google_client_config.default.access_token
  cluster_ca_certificate = base64decode(google_container_cluster.autopilot.master_auth[0].cluster_ca_certificate)
}

provider "helm" {
  kubernetes {
    host                   = "https://${google_container_cluster.autopilot.endpoint}"
    token                  = data.google_client_config.default.access_token
    cluster_ca_certificate = base64decode(google_container_cluster.autopilot.master_auth[0].cluster_ca_certificate)
  }
}

# ── GKE Autopilot Cluster ─────────────────────────────────────────────────────

resource "google_container_cluster" "autopilot" {
  name     = var.cluster_name
  location = var.region

  # Autopilot: Google manages nodes, scaling, and infrastructure
  enable_autopilot = true

  # Required for Autopilot
  ip_allocation_policy {}

  release_channel {
    channel = "REGULAR"
  }

  # Enable Workload Identity for pod-level IAM
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }
}

# ── Monitoring Namespace ──────────────────────────────────────────────────────

resource "kubernetes_namespace" "monitoring" {
  metadata {
    name = "monitoring"
  }
  depends_on = [google_container_cluster.autopilot]
}

# ── Prometheus + AlertManager ─────────────────────────────────────────────────

resource "helm_release" "prometheus" {
  name       = "prometheus"
  repository = "https://prometheus-community.github.io/helm-charts"
  chart      = "kube-prometheus-stack"
  namespace  = kubernetes_namespace.monitoring.metadata[0].name
  version    = "62.3.1"
  wait       = false

  values = [
    <<-EOF
    alertmanager:
      config:
        global:
          resolve_timeout: 5m
        route:
          group_by: ['alertname', 'job']
          group_wait: 10s
          group_interval: 10s
          repeat_interval: 1h
          receiver: 'webhook-solver'
          routes:
            - receiver: 'webhook-solver'
              matchers:
                - severity="critical"
        receivers:
          - name: 'webhook-solver'
            webhook_configs:
              - url: '${var.webhook_url}'
                send_resolved: true
    prometheus:
      prometheusSpec:
        ruleSelectorNilUsesHelmValues: false
        serviceMonitorSelectorNilUsesHelmValues: false
        podMonitorSelectorNilUsesHelmValues: false
        probeSelectorNilUsesHelmValues: false
    EOF
  ]

  depends_on = [kubernetes_namespace.monitoring]
}

# ── Outputs ───────────────────────────────────────────────────────────────────

output "cluster_endpoint" {
  description = "GKE Autopilot cluster endpoint"
  value       = google_container_cluster.autopilot.endpoint
  sensitive   = true
}

output "kubeconfig_command" {
  description = "Command to configure kubectl"
  value       = "gcloud container clusters get-credentials ${var.cluster_name} --region ${var.region} --project ${var.project_id}"
}
