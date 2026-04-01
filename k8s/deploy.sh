#!/bin/bash

# Production K8s Deployment Script for AutoInfraRemediation

set -e

NAMESPACE="auto-remediation"
DOCKER_IMAGE="auto-remediation:latest"

echo "🚀 Deploying AutoInfraRemediation to Kubernetes"
echo "================================================="

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo "❌ kubectl is not installed or not in PATH"
    exit 1
fi

# Check if we can connect to cluster
if ! kubectl cluster-info &> /dev/null; then
    echo "❌ Cannot connect to Kubernetes cluster"
    echo "   Make sure kubeconfig is set correctly"
    exit 1
fi

echo "✅ Connected to Kubernetes cluster"

# Build Docker image
echo "📦 Building Docker image..."
docker build -f ../Dockerfile.prod -t $DOCKER_IMAGE ..

# Load image to kind/minikube if local development
if kubectl get nodes | grep -q "kind\|minikube"; then
    echo "📥 Loading image to local cluster..."
    if command -v kind &> /dev/null; then
        kind load docker-image $DOCKER_IMAGE
    elif command -v minikube &> /dev/null; then
        eval $(minikube docker-env)
        docker build -f ../Dockerfile.prod -t $DOCKER_IMAGE ..
    fi
fi

# Apply manifests
echo "📋 Applying Kubernetes manifests..."

echo "  → Creating namespace and secrets..."
kubectl apply -f 01-namespace-secrets.yaml

echo "  → Deploying database and Redis..."
kubectl apply -f 02-database-redis.yaml

echo "  → Setting up RBAC..."
kubectl apply -f 04-rbac.yaml

echo "  → Deploying Temporal and application..."
kubectl apply -f 03-temporal-app.yaml

# Wait for deployment
echo "⏳ Waiting for services to start..."

echo "  → Waiting for PostgreSQL..."
kubectl wait --for=condition=ready pod -l app=postgres -n $NAMESPACE --timeout=120s

echo "  → Waiting for Redis..."
kubectl wait --for=condition=ready pod -l app=redis -n $NAMESPACE --timeout=60s

echo "  → Waiting for Temporal..."
kubectl wait --for=condition=ready pod -l app=temporal -n $NAMESPACE --timeout=180s

echo "  → Waiting for AutoRemediation application..."
kubectl wait --for=condition=ready pod -l app=auto-remediation -n $NAMESPACE --timeout=120s

# Get service info
echo ""
echo "✅ Deployment complete!"
echo "======================="

kubectl get pods -n $NAMESPACE

echo ""
echo "📡 Service Access:"

# Check if ingress is available
if kubectl get ingress auto-remediation-ingress -n $NAMESPACE &> /dev/null; then
    INGRESS_HOST=$(kubectl get ingress auto-remediation-ingress -n $NAMESPACE -o jsonpath='{.spec.rules[0].host}')
    echo "  → Ingress URL: https://$INGRESS_HOST"
else
    echo "  → Port forward: kubectl port-forward svc/auto-remediation-service -n $NAMESPACE 8001:80"
    echo "  → Then access: http://localhost:8001"
fi

echo ""
echo "🔍 Useful commands:"
echo "  → View logs: kubectl logs -f deployment/auto-remediation -n $NAMESPACE"
echo "  → Check health: kubectl port-forward svc/auto-remediation-service -n $NAMESPACE 8001:80"
echo "  → Scale app: kubectl scale deployment auto-remediation --replicas=3 -n $NAMESPACE"
echo "  → Delete all: kubectl delete namespace $NAMESPACE"

echo ""
echo "✨ AutoInfraRemediation is ready!"