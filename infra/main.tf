terraform {
  required_providers {
    kind = {
      source  = "tehcyx/kind"
      version = "~> 0.6.0"
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
}

provider "kind" {}

resource "kind_cluster" "default" {
  name           = "auto-remediation-cluster"
  node_image     = "kindest/node:v1.31.2"
  wait_for_ready = true

  kind_config {
    kind        = "Cluster"
    api_version = "kind.x-k8s.io/v1alpha4"

    node {
      role = "control-plane"
      # Expose ports if necessary for external access
      extra_port_mappings {
        container_port = 30080
        host_port      = 8088
        protocol       = "TCP"
      }
    }
  }
}

provider "kubernetes" {
  host                   = kind_cluster.default.endpoint
  client_certificate     = kind_cluster.default.client_certificate
  client_key             = kind_cluster.default.client_key
  cluster_ca_certificate = kind_cluster.default.cluster_ca_certificate
}

provider "helm" {
  kubernetes {
    host                   = kind_cluster.default.endpoint
    client_certificate     = kind_cluster.default.client_certificate
    client_key             = kind_cluster.default.client_key
    cluster_ca_certificate = kind_cluster.default.cluster_ca_certificate
  }
}

resource "kubernetes_namespace" "monitoring" {
  metadata {
    name = "monitoring"
  }
  depends_on = [kind_cluster.default]
}

resource "helm_release" "prometheus" {
  name       = "prometheus"
  repository = "https://prometheus-community.github.io/helm-charts"
  chart      = "kube-prometheus-stack"
  namespace  = kubernetes_namespace.monitoring.metadata[0].name
  version    = "62.3.1"
  wait       = false # Set to false to not block tf apply for too long, metrics will come up

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
          # Send all alerts to our python service
          routes:
            - receiver: 'webhook-solver'
              matchers:
                - severity="critical"
        receivers:
          - name: 'webhook-solver'
            webhook_configs:
              - url: 'http://host.docker.internal:8000/alert'
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
