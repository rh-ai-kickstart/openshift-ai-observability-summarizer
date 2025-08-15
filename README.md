# OpenShift AI Observability Summarizer

[![CNCF Compatible](https://img.shields.io/badge/CNCF%20Compatible-Yes-blue.svg)](https://www.cncf.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Build Status](https://github.com/rh-ai-kickstart/openshift-ai-observability-summarizer/actions/workflows/build-and-push.yml/badge.svg)](https://github.com/rh-ai-kickstart/openshift-ai-observability-summarizer/actions)


<img src="docs/img/logo.png" alt="OpenShift AI Observability Summarizer" width="200"/>


[Design Document](https://docs.google.com/document/d/1bXBCL4fbPlRqQxwhGX1p12CS_E6-9oOyFnYSpbQskyI/edit?usp=sharing)

## Overview

OpenShift AI Observability Summarizer is an **open source, CNCF-style project** for advanced monitoring and automated summarization of AI model and OpenShift cluster metrics. It provides an interactive dashboard for analyzing metrics collected from Prometheus and generating human-readable, AI-powered insights and reports.

- **Monitors vLLM deployments, OpenShift fleet health, and GPU utilization**
- **Generates actionable summaries using LLMs**
- **Supports alerting, notifications, and exportable reports**

---

## Table of Contents

- [Features](#features)
- [GPU Monitoring](#gpu-monitoring)
- [Architecture](#architecture)
- [Getting Started](#getting-started)
- [Usage](#usage)
- [Build & Deploy](#build--deploy)
- [Local Development](#local-development-via-port-forwarding)
- [Testing](#testing)
- [Semantic Versioning](#semantic-versioning)
- [Contributing](#contributing)
- [Community](#community)
- [License](#license)

---


## Features

### **1. vLLM Monitoring**
- Visualize core vLLM metrics (GPU usage, latency, request volume, etc.)
- Dynamic DCGM GPU metrics discovery (temperature, power, memory)
- Real-time performance analysis and anomaly detection

### **2. OpenShift Fleet Monitoring** 
- Cluster-wide and namespace-scoped metric analysis
- GPU & Accelerators fleet monitoring with comprehensive DCGM metrics
- Workloads, Storage, Networking, and Application Services monitoring
- Enhanced unit formatting (°C, Watts, GB, MB/s, etc.)

### **3. AI-Powered Insights**
- Generate summaries using fine-tuned Llama models
- Chat with an MLOps assistant based on real metrics
- Support for both internal and external LLM models

### **4. Report Generation**
- Export analysis as HTML, PDF, or Markdown reports
- Time-series charts and metric visualizations
- Automated metric calculations and trend analysis

### **5. Alerting & Notifications**
- Set up alerts for vLLM models and OpenShift metrics  
- Slack notifications when alerts are triggered
- Custom alert thresholds and conditions

### **6. Distributed Tracing Integration**
- Tracing support with OpenTelemetry and Tempo to monitor request flows across your AI services.

### **6. AI Assistant Integration (MCP Server)**
- **Model Context Protocol (MCP) server** for AI assistants (Claude Desktop, Cursor IDE)
- **Natural language analysis** - ask questions like "What is the GPU temperature?"
- **Real-time data access** - connects directly to Prometheus/Thanos
- **AI-powered insights** - full LLM integration for intelligent metric analysis

📖 **Quick Setup**: 
```bash
cd src/mcp_server && python setup_integration.py
```
See [`src/mcp_server/README.md`](src/mcp_server/README.md) for complete documentation.

---

### GPU Monitoring

### **DCGM Metrics Support**
Automatically discovers and monitors:
- **Temperature**: GPU core and memory temperature (°C)
- **Power**: Real-time power consumption (Watts)  
- **Memory**: GPU memory usage (GB) and utilization (%)
- **Energy**: Total energy consumption (Joules)
- **Performance**: GPU utilization, clock speeds (MHz)

### **Fleet View**
Monitor GPU health across your entire OpenShift cluster:
- Cluster-wide GPU temperature averaging
- Power consumption trends
- Memory usage patterns
- Vendor/model detection and inventory

---

## Architecture

### Core Components

### **Monitoring Stack**
- **Prometheus**: Prometheus scrapes the /metrics endpoint offered by vLLM. It can store metrics itself in its own time-series database on a local disk which is
                  highly optimized for fast queries on recent data. This is perfect for real-time monitoring and alerting but is not
                  ideal for long term and multi-year storage. This is where Thanos Querier comes in.
- **Thanos Querier**: Extends Prometheus by solving the problem of long-term retention. Thanos is capable of taking data blocks that Prometheus saves to
                      its local disk and uploading them to inexpensive and durable object storage, like Amazon S3, Google Cloud Storage, or Azure Blob Storage.
                      Querier gives you a cost-effective way of retaining years of metrics data available for historical analysis and trend reporting.
                      Querier sidecars run alongside your Prometheus servers, providing access to real-time and recent metrics.                  
- **DCGM**: GPU monitoring and telemetry
- **Streamlit UI**: Multi-dashboard interface (vLLM, OpenShift, Chat)
- **FastAPI Backend**: metrics-api for web UI and report generation
- **MCP Server**: Model Context Protocol server for AI assistant integration
- **Report Generator**: PDF/HTML/Markdown export capabilities
- **llm-service:** LLM inference (Llama models)
- **llama-stack:** Backend API
- **vLLM:** Model serving, exports Prometheus /metrics
- **Prometheus/Thanos:** Metrics scraping, long-term storage
- **DCGM:** GPU monitoring
- **Streamlit UI:** Multi-dashboard frontend
- **MCP:** Metric Collection & Processing backend
- **Report Generator:** PDF/HTML/Markdown export

![Architecture](docs/img/arch-2.jpg)

### **Key Components**
1. **vLLM Dashboard**: Monitor model performance, GPU usage, latency
2. **OpenShift Dashboard**: Fleet monitoring with cluster-wide and namespace views
3. **Chat Interface**: Interactive Q&A with metrics-aware AI assistant
4. **MCP Server**: AI assistant integration via Model Context Protocol
5. **Report Generator**: Automated analysis reports in multiple formats

---

## Getting Started

### Prerequisites

- OpenShift cluster with GPU nodes (for DCGM metrics)
- `oc` CLI with cluster-admin permissions
- `helm` v3.x
- `yq` (YAML processor)
- Deployed Prometheus/Thanos
- Operators for distributed tracing
  - Red Hat Build of OpenTelemetry Operator
  - Tempo Operator
  - Cluster Observability Operator
- (Optional) DCGM exporter for GPU monitoring
- (Optional) Slack Webhook URL for alerting ([How to create a Webhook for your Slack Workspace](https://api.slack.com/messaging/webhooks))


### Installing the OpenShift AI Observability Summarizer

Use the included `Makefile` to install everything:
```bash
make install NAMESPACE=your-namespace
```
This will install the project with the default LLM deployment, `llama-3-2-3b-instruct`.

### Choosing different models

To see all available models:
```bash
make list-models
```
```
(Output)
model: llama-3-1-8b-instruct (meta-llama/Llama-3.1-8B-Instruct)
model: llama-3-2-1b-instruct (meta-llama/Llama-3.2-1B-Instruct)
model: llama-3-2-1b-instruct-quantized (RedHatAI/Llama-3.2-1B-Instruct-quantized.w8a8)
model: llama-3-2-3b-instruct (meta-llama/Llama-3.2-3B-Instruct)
model: llama-3-3-70b-instruct (meta-llama/Llama-3.3-70B-Instruct)
model: llama-guard-3-1b (meta-llama/Llama-Guard-3-1B)
model: llama-guard-3-8b (meta-llama/Llama-Guard-3-8B)
```
You can use the `LLM` flag during installation to set a model from this list for deployment:
```
make install NAMESPACE=your-namespace LLM=llama-3-2-3b-instruct 
```

### With GPU tolerations
```bash
make install NAMESPACE=your-namespace LLM=llama-3-2-3b-instruct LLM_TOLERATION="nvidia.com/gpu"
```

### With safety models
```bash
make install NAMESPACE=your-namespace \
  LLM=llama-3-2-3b-instruct LLM_TOLERATION="nvidia.com/gpu" \
  SAFETY=llama-guard-3-8b SAFETY_TOLERATION="nvidia.com/gpu"
```

### With alerting
```bash
make install NAMESPACE=your-namespace ALERTS=TRUE
```
Enabling alerting will deploy alert rules, a cron job to monitor vLLM metrics, and AI-powered Slack notifications.

### Accessing the Application

The default configuration deploys:
- **llm-service** - LLM inference 
- **llama-stack** - Backend API
- **pgvector** - Vector database
- **metric-mcp** - Metrics collection & processing API
- **metric-ui** - Multi-dashboard Streamlit interface
- **OpenTelemetry Collector** - Distributed tracing collection
- **Tempo** - Trace storage and analysis
- **MinIO** - Object storage for traces

Navigate to your **Openshift Cluster --> Networking --> Route** and you should be able to see the route for your application. You can also navigate to **Observe > Traces** in the OpenShift console to view traces.

On terminal you can access the route with -

```bash
oc get route

NAME              HOST/PORT                                                               PATH   SERVICES        PORT   TERMINATION     WILDCARD
metric-ui-route   metric-ui-route-llama-1.apps.tsisodia-spark.2vn8.p1.openshiftapps.com          metric-ui-svc   8501   edge/Redirect   None
```

### Openshift Summarizer Dashboard 
![UI](docs/img/os.png)

### vLLM SUmmarizer Dashboard 
![UI](docs/img/vllm.png)

### Chat with Prometheus 
![UI](docs/img/chat.png)

### Report Generated 
![UI](docs/img/report.png)


To uninstall:

```bash
make uninstall NAMESPACE=metric-summarizer
```

---

## Usage

### **Multi-Dashboard Interface**
Access via the OpenShift route: `oc get route ui`

#### **vLLM Metric Summarizer**
1. Select your AI model and namespace
2. Choose time range for analysis  
3. Click **Analyze Metrics** for AI-powered insights
4. Download reports in HTML/PDF/Markdown format

#### **OpenShift Metrics Dashboard**
1. Choose metric category (Fleet Overview, GPU & Accelerators, etc.)
2. Select scope: Cluster-wide or Namespace-scoped
3. Analyze performance with AI-generated summaries
4. Monitor GPU temperature, power usage, and utilization across fleet

#### **Chat with Prometheus**
1. Ask natural language questions about your metrics
2. Get specific PromQL queries and insights
3. Interactive troubleshooting with metrics context

#### **Key Monitoring Categories**
- **Fleet Overview**: Pods, CPU, Memory, GPU temperature
- **GPU & Accelerators**: Temperature, power, utilization, memory (GB)  
- **Workloads & Pods**: Container metrics, restarts, failures
- **Storage & Networking**: I/O rates, network throughput
- **Application Services**: HTTP metrics, endpoints, errors

#### Generate Reports
You can generate detailed metric reports in multiple formats directly from the dashboard:

- **HTML Report**: Interactive and visually rich, suitable for sharing or archiving.
- **PDF Report**: Print-ready, ideal for documentation or compliance needs.
- **Markdown Report**: Lightweight, easy to edit or integrate into wikis and documentation.

To generate a report:
1. Complete your analysis in either the vLLM or OpenShift dashboard.
2. Click the **Download Report** button.
3. Choose your preferred format (HTML, PDF, or Markdown).
4. The report will be generated and downloaded automatically, containing charts, summaries, and key insights from your session.

---

## Build & Deploy

The project includes a comprehensive Makefile that simplifies building, pushing, and deploying the application components.

### Building and Pushing Container Images

The application consists of multiple services that need to be built as container images for OpenShift deployment.

#### **Build All Images**

```bash
# Build all container images (metrics-api, metric-ui, metric-alerting)
make build

# Build with custom tag
make build TAG=v1.0.0

# Build with custom registry
make build REGISTRY=your-registry.com/your-org
```

#### **Build Individual Components**

```bash
# Build FastAPI Backend (metrics-api)
make build-metrics-api

# Build Streamlit UI (metric-ui)
make build-ui

# Build Alerting Service (metric-alerting)
make build-alerting
```

#### **Push Images to Registry**

```bash
# Push all images to registry
make push

# Push with custom tag
make push TAG=v1.0.0

# Push individual components
make push-metrics-api
make push-ui
make push-alerting
```

#### **Complete Build and Push Workflow**

```bash
# Build and push all images in one command
make build-and-push

# With custom configuration
make build-and-push TAG=v1.0.0 REGISTRY=your-registry.com/your-org
```

### Deploy to OpenShift

#### **Basic Deployment**

```bash
# Deploy to OpenShift namespace
make deploy NAMESPACE=your-namespace

# Deploy with alerting enabled
make deploy-with-alerts NAMESPACE=your-namespace
```

#### **Complete Build, Push, and Deploy Workflow**

```bash
# Complete workflow: build → push → deploy
make build-deploy NAMESPACE=your-namespace

# Complete workflow with alerting
make build-deploy-alerts NAMESPACE=your-namespace
```

#### **Deployment Management**

```bash
# Check deployment status
make status NAMESPACE=your-namespace

# Uninstall deployment
make uninstall NAMESPACE=your-namespace
```

### Configuration Options

The Makefile supports various configuration options via environment variables:

```bash
# Set custom registry
export REGISTRY=your-registry.com/your-org

# Set custom tag
export TAG=v1.0.0

# Set target platform
export PLATFORM=linux/amd64

# Show current configuration
make config
```

### Local Development

For local development with port-forwarding:

```bash
# Set up local development environment
make deploy-local
```

This will run the `./scripts/local-dev.sh` script to set up port-forwarding to Llamastack, llm-service, and Thanos.

### Available Models

```bash
# List available models for deployment
make list-models
```

### Cleanup

```bash
# Clean up local images
make clean
```

### Automated CI/CD Build

The project includes GitHub Actions workflow (`.github/workflows/build-and-push.yml`) that automatically builds and pushes container images when changes are pushed to the repository.

**Image naming convention:** `v1.0.{GITHUB_RUN_NUMBER}`

---

## Local Development via Port-Forwarding

For local development of the metrics API/UI and MCP server, use the unified development environment script that handles port-forwarding to Llamastack, LLM service, and Thanos.

**Pre-requisites**:
1. You have a deployment on the cluster already.
2. You are logged into the cluster and can execute `oc` commands against the cluster.

### Quick Setup with Makefile

The easiest way to set up local development is using the Makefile:

```bash
# Set up local development environment
make deploy-local
```

This will run the `./scripts/local-dev.sh` script automatically.

### Manual Setup

If you prefer to run the script manually, follow these steps:

1. **Make sure you are logged into the cluster and can execute `oc` commands against the cluster.**
2. Install `uv` by following instructions on the [uv website](https://github.com/astral-sh/uv)
3. Sync up the environment and development dependencies using `uv` in the base directory:
### Quick Start
```bash
# 1. Setup environment
uv sync --group dev
source .venv/bin/activate

# 2. Export your namespace  
export LLM_NAMESPACE=<DESIRED_NAMESPACE>

# 3. Start development environment (includes all port forwarding)
./scripts/local-dev.sh
```

### What the script does:
- ✅ **Port forwards Prometheus/Thanos** (localhost:9090)
- ✅ **Port forwards LLM server** (localhost:8321) 
- ✅ **Starts metrics API** (localhost:8000)
- ✅ **Starts Streamlit UI** (localhost:8501)
- ✅ **Configures environment** for MCP server development

### For MCP/AI Assistant Development
After running `scripts/local-dev.sh`, you can:

```bash
# Configure AI assistants (Claude Desktop + Cursor IDE)
cd src/mcp_server
python setup_integration.py

# Test MCP server
obs-mcp-server --test-config
```

The output should look like this:
![Command Output](docs/img/local-dev-expected.png)

#### Macos weasyprint install

**Still verifying whether we need this setup or not as weasyprint is installed using `uv` in previous step.**

In order to run the metrics API locally you'll need to install weasyprint:
1. Install via brew `brew install weasyprint`
2. Ensure installation `weasyprint --version`
3. Set **DYLD_FALLBACK_LIBRARY_PATH** `export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib:$DYLD_FALLBACK_LIBRARY_PATH`

## Running Tests with Pytest

The test suite is located in the `tests/` directory, with the tests for each service in their respective directories.

1. Using `uv`, sync up the test dependencies listed in `pyproject.toml`:

```bash
# Create virtual environment
uv sync --group test
```

2. Use the `pytest` command go run all tests

```bash
# Run all tests with verbose output and coverage
uv run pytest -v --cov=src --cov-report=html --cov-report=term

# Run only MCP tests
uv run pytest -v tests/mcp/

# Run specific test file
uv run pytest -v tests/mcp/test_api_endpoints.py
```

To view a detailed coverage report after generating, open `htmlcov/index.html`.

---


## Semantic Versioning

This project uses automated semantic versioning based on commit message conventions.

### Version Bump Rules

- **Major (`X`.0.0)**: Breaking changes
  - Keywords: `BREAKING CHANGE:`, `breaking:`, `!:`, `major:`
  - Example: `refactor!: redesign API endpoints`

- **Minor (X.`Y`.0)**: New features
  - Keywords: `feat:`, `feature:`, `add:`, `minor:`
  - Example: `feat: add user authentication`

- **Patch (X.Y.`Z`)**: Bug fixes and other changes
  - Keywords: Any other commit message
  - Example: `fix: resolve login timeout`

### How It Works

1. When PRs are merged to `main` or `dev`, the build workflow analyzes commit messages
2. Automatically calculates the next version number
3. Updates container image tags, Helm charts, and Makefile

### Examples

#### Major Version Bump (`X`.0.0)

Breaking changes that require major version bump:

```bash
# Using "BREAKING CHANGE:" in commit message body
git commit -m "refactor: change API structure

BREAKING CHANGE: API endpoints restructured"

# Using "!" in commit type
git commit -m "refactor!: change API structure"

# Using "major:" keyword
git commit -m "major: remove deprecated features"
```

#### Minor Version Bump (X.`Y`.0)

New features that require minor version bump:

```bash
# New feature addition
git commit -m "feat: add metrics dashboard"

# Using "feature:" keyword
git commit -m "feature: implement user authentication"

# Using "add:" keyword
git commit -m "add: support for new GPU metrics"

# Using "minor:" keyword
git commit -m "minor: enhance UI with new charts"
```

#### Patch Version Bump (X.Y.`Z`)

Bug fixes and other changes that require patch version bump:

```bash
# Bug fix
git commit -m "fix: resolve memory leak in alerting"

# Documentation update
git commit -m "docs: update installation guide"

# Chore or maintenance
git commit -m "chore: update dependencies"

# Any commit without specific keywords
git commit -m "resolve memory leak in alerting"
```

---

## Contributing

We welcome contributions and feedback! Please open issues or submit PRs to improve this dashboard or expand model compatibility.

See [CONTRIBUTING.md](CONTRIBUTING.md) for full contribution guidelines.

---

## Community

- [GitHub Discussions](https://github.com/rh-ai-kickstart/openshift-ai-observability-summarizer/discussions)
- [CNCF Landscape](https://landscape.cncf.io/)
- [OpenShift AI](https://www.redhat.com/en/technologies/cloud-computing/openshift/openshift-ai)
- [Prometheus](https://prometheus.io/)
- [Streamlit](https://streamlit.io/)

---

## License

Licensed under the [MIT License](LICENSE).

---

