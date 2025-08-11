#!/bin/bash

# Deploy Korrel8r correlation engine to minikube cluster

set -e

echo "🚀 Deploying Korrel8r PoC to minikube..."

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo "❌ kubectl not found. Please install kubectl first."
    exit 1
fi

# Check if we can connect to cluster
if ! kubectl cluster-info &> /dev/null; then
    echo "❌ Cannot connect to Kubernetes cluster. Is minikube running?"
    exit 1
fi

# Create namespace
echo "📁 Creating namespace..."
kubectl apply -f manifests/namespace.yaml

# Check if endpoints config exists
if [ ! -f "config/endpoints.yaml" ]; then
    echo "⚠️  endpoints.yaml not found. Creating from example..."
    cp config/endpoints.example.yaml config/endpoints.yaml
    echo "📝 Please edit config/endpoints.yaml with your Prometheus and Tempo URLs"
    echo "   Example for minikube:"
    echo "   prometheus-url: http://$(minikube ip):30090"
    echo "   tempo-url: http://$(minikube ip):30320"
    exit 1
fi

# Apply endpoints configuration
echo "⚙️  Applying endpoints configuration..."
kubectl apply -f config/endpoints.yaml

# Apply Korrel8r rules
echo "📋 Applying correlation rules..."
kubectl apply -f config/korrel8r-rules.yaml

# Apply RBAC
echo "🔐 Setting up RBAC..."
kubectl apply -f manifests/korrel8r-rbac.yaml

# Deploy Korrel8r
echo "🔄 Deploying Korrel8r..."
kubectl apply -f manifests/korrel8r-deployment.yaml

# Wait for deployment to be ready
echo "⏳ Waiting for Korrel8r to be ready..."
kubectl wait --for=condition=available --timeout=300s deployment/korrel8r -n ai-observability-poc

# Check if it's running
echo "✅ Checking Korrel8r status..."
kubectl get pods -n ai-observability-poc -l app=korrel8r

echo ""
echo "🎉 Korrel8r deployed successfully!"
echo ""
echo "To test the deployment:"
echo "  kubectl port-forward svc/korrel8r 8080:8080 -n ai-observability-poc"
echo "  curl http://localhost:8080/api/v1alpha1/status"
echo ""
echo "To view logs:"
echo "  kubectl logs -f deployment/korrel8r -n ai-observability-poc"