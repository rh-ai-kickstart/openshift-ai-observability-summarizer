#!/bin/bash

set -e

echo "🚀 Deploying AI Observability PoC with LLM integration..."

if ! command -v kubectl &> /dev/null; then
    echo "❌ kubectl not found. Please install kubectl first."
    exit 1
fi

if ! kubectl cluster-info &> /dev/null; then
    echo "❌ Cannot connect to Kubernetes cluster. Is minikube running?"
    exit 1
fi

# Check if LLM service exists in llm-d namespace
echo "🔍 Checking for LLM service in llm-d namespace..."
if kubectl get svc llm-d-inference-gateway-istio -n llm-d &> /dev/null; then
    echo "✅ Found LLM service: llm-d-inference-gateway-istio in llm-d namespace"
    LLM_NAMESPACE="llm-d"
    
    LLM_PORT=$(kubectl get svc llm-d-inference-gateway-istio -n llm-d -o jsonpath='{.spec.ports[0].port}')
    echo "   Service: llm-d-inference-gateway-istio.llm-d.svc.cluster.local:${LLM_PORT}"
else
    echo "⚠️  LLM service 'llm-d-inference-gateway-istio' not found in llm-d namespace."
    echo "   Proceeding with default configuration - you can update config/llm-config.yaml manually"
    LLM_NAMESPACE="llm-d"  # Still use llm-d as default
fi

echo "📁 Creating namespace..."
kubectl apply -f manifests/namespace.yaml

if [ ! -f "config/endpoints.yaml" ]; then
    echo "⚠️  Creating endpoints.yaml from example..."
    cp config/endpoints.example.yaml config/endpoints.yaml
    echo "📝 Please update config/endpoints.yaml with your actual endpoints"
    echo "   Current LLM service will be auto-configured"
fi

echo "⚙️  Applying configuration..."
kubectl apply -f config/endpoints.yaml
kubectl apply -f config/korrel8r-rules.yaml

echo "🧠 Configuring LLM integration..."
sed "s/llm-d-inference-gateway-istio.llm-d.svc/llm-d-inference-gateway-istio.$LLM_NAMESPACE.svc/g" \
    config/llm-config.yaml | kubectl apply -f -

# Deploy Korrel8r
echo "🔄 Deploying Korrel8r correlation engine..."
kubectl apply -f manifests/korrel8r-rbac.yaml
kubectl apply -f manifests/korrel8r-deployment.yaml

echo "🔧 Deploying backend service with LLM integration..."
kubectl apply -f manifests/observability-backend-deployment.yaml

echo "🎨 Deploying Enhanced UI..."
# Update UI deployment to use backend service
sed 's/MCP_API_URL.*$/BACKEND_API_URL\n          value: "http:\/\/observability-backend:8000"/g' \
    manifests/ui.yaml | kubectl apply -f -

echo "⏳ Waiting for services to be ready..."
kubectl wait --for=condition=available --timeout=300s deployment/korrel8r -n ai-observability-poc
kubectl wait --for=condition=available --timeout=300s deployment/observability-backend -n ai-observability-poc  
kubectl wait --for=condition=available --timeout=300s deployment/observe-ui -n ai-observability-poc

echo "✅ Checking deployment status..."
kubectl get pods -n ai-observability-poc

echo "🧠 Testing LLM connectivity..."
kubectl exec -n ai-observability-poc deployment/observability-backend -- \
    python -c "
import aiohttp
import asyncio
import os

async def test_llm():
    try:
        llm_url = os.getenv('LLM_ENDPOINT')
        print(f'Testing LLM at: {llm_url}')
        async with aiohttp.ClientSession() as session:
            async with session.get(f'{llm_url}/health', timeout=10) as resp:
                print(f'LLM Health Check: {resp.status}')
    except Exception as e:
        print(f'LLM Test Failed: {e}')

asyncio.run(test_llm())
" || echo "⚠️  LLM connectivity test failed - check configuration"

echo ""
echo "🎉 Enhanced PoC with LLM deployed successfully!"
echo ""
echo "🌐 Access the Enhanced UI:"
echo "  kubectl port-forward svc/observe-ui 8501:8501 -n ai-observability-poc"
echo "  Visit: http://localhost:8501"
echo ""
echo "🔧 Test Backend API:"
echo "  kubectl port-forward svc/observability-backend 8000:8000 -n ai-observability-poc"  
echo "  curl http://localhost:8000/health"
echo ""
echo "🧠 LLM Integration:"
echo "  LLM Endpoint: llm-d-inference-gateway-istio.$LLM_NAMESPACE.svc.cluster.local:80"
echo "  Backend will use this for AI-powered correlation analysis"
echo ""
echo "📊 Ready to test correlation with LLM insights!"
