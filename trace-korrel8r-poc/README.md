# Unified Observability PoC

This PoC demonstrates correlation between Prometheus metrics and Tempo traces using Korrel8r on minikube.

## Prerequisites

- Minikube cluster running
- Existing Prometheus endpoint accessible from cluster
- Existing Tempo endpoint accessible from cluster
- kubectl configured for your minikube cluster

## Quick Start

```bash
# 1. Configure your endpoints
cp config/endpoints.example.yaml config/endpoints.yaml
# Edit endpoints.yaml with your Prometheus/Tempo URLs

# 2. Deploy complete PoC (recommended)
./scripts/deploy-poc.sh

# OR deploy Korrel8r correlation engine only
./scripts/deploy-korrel8r.sh

# 3. Access the UI
kubectl port-forward svc/observe-ui 8501:8501 -n ai-observability-poc
```

## Directory Structure

```
poc/
├── README.md                    # This file
├── manifests/                   # Kubernetes manifests
│   ├── namespace.yaml
│   ├── korrel8r-deployment.yaml
│   ├── korrel8r-rbac.yaml
│   ├── observability-backend-deployment.yaml
│   └── ui.yaml
├── config/                      # Configuration files
│   ├── endpoints.example.yaml
│   ├── korrel8r-rules.yaml
│   └── llm-config.yaml
├── ui/                         # Enhanced Streamlit UI
│   └── observe_ui.py
├── backend/                    # Backend services
│   ├── observability_service.py
│   ├── llm_client.py
│   └── requirements.txt
└── scripts/                    # Deployment scripts
    ├── deploy-poc.sh
    ├── deploy-korrel8r.sh
```

## Deployment Options

### Complete PoC Deployment
```bash
./scripts/deploy-poc.sh
```
Deploys namespace, Korrel8r, backend service, and enhanced UI.

### Korrel8r Only
```bash
./scripts/deploy-korrel8r.sh
```
Deploys only the correlation engine for testing.

## What This PoC Demonstrates

1. **Metrics → Traces Correlation**: Click on a metric spike and see related traces
2. **Trace → Metrics Correlation**: Select a slow trace and see related metric patterns
3. **Natural Language Queries**: Ask "What traces are related to high CPU usage?"
4. **Cross-Signal Analysis**: Unified view of metrics and traces in one dashboard

## Troubleshooting

```bash
# Check PoC deployment status
kubectl get pods -n ai-observability-poc
kubectl get svc -n ai-observability-poc

# View logs
kubectl logs -f deployment/korrel8r -n ai-observability-poc
kubectl logs -f deployment/observe-ui -n ai-observability-poc
kubectl logs -f deployment/observability-backend -n ai-observability-poc

# Test correlation engine
kubectl exec -it deployment/korrel8r -n ai-observability-poc -- wget -qO- http://localhost:8080/health
```

## Next Steps

After validating this PoC:
1. Add Loki integration for logs
2. Enhance LLM-powered analysis
3. Deploy to production OpenShift cluster
