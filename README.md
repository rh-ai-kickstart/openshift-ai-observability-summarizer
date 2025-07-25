# AI Observability Metrics Summarizer

<img src="docs/img/logo.png" alt="UI" style="width:200px; height:200px;"/>


[Design Document](https://docs.google.com/document/d/1bXBCL4fbPlRqQxwhGX1p12CS_E6-9oOyFnYSpbQskyI/edit?usp=sharing)

This application provides an interactive multi-dashboard interface to **analyze AI model performance metrics and OpenShift cluster metrics** collected from Prometheus and generate **human-like summaries using LLM models** deployed on OpenShift AI.

It helps teams **monitor vLLM deployments, OpenShift fleet health, and GPU utilization** with **actionable AI-powered insights** — all automatically.

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

---

## GPU Monitoring

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

### **Core backend components**
- **llm-service**: LLM inference services (Llama models)
- **llama-stack**: LlamaStack backend API
- **vLLM**: vLLM fronts the model and makes metrics available at the /metrics endpoint which Prometheus can coveniently scrape.

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
- **MCP (Metric Collection & Processing)**: Backend API for metric analysis
- **Report Generator**: PDF/HTML/Markdown export capabilities

![Architecture](docs/img/arch-2.jpg)

### **Key Components**
1. **vLLM Dashboard**: Monitor model performance, GPU usage, latency
2. **OpenShift Dashboard**: Fleet monitoring with cluster-wide and namespace views
3. **Chat Interface**: Interactive Q&A with metrics-aware AI assistant
4. **Report Generator**: Automated analysis reports in multiple formats

---

## Prerequisites

- OpenShift cluster with **GPU nodes** (for DCGM metrics)
- `oc` CLI configured with cluster-admin permissions
- `helm` v3.x installed
- `yq` for YAML processing (`brew install yq`)
- **Prometheus/Thanos** deployed for metrics collection
- **DCGM exporter** for GPU monitoring (optional but recommended)
- Slack Webhook URL for optional alerting ([How to create a Webhook for your Slack Workspace](https://api.slack.com/messaging/webhooks))

---


## Installing the OpenShift AI Observability Summarizer

Use the included `Makefile` to install everything:
```bash
cd deploy/helm
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

### 

This deploys:
- **llm-service** - LLM inference 
- **llama-stack** - Backend API
- **pgvector** - Vector database
- **metric-mcp** - Metrics collection & processing API
- **metric-ui** - Multi-dashboard Streamlit interface

Navigate to your **Openshift Cluster --> Networking --> Route** and you should be able to see the route for your application.

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

## Using the App

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

## Container Build & Deployment

### Building the Container Images

The application consists of multiple services that need to be built as container images for Kubernetes deployment.

#### **Build FastAPI Backend (metric-mcp)**

**Using Podman:**
```bash
# Navigate to the metric_ui directory (important for build context)
cd metric_ui

# Build for linux/amd64 platform (required for most K8s clusters)
podman buildx build --platform linux/amd64 \
  -f api/Dockerfile \
  -t quay.io/ecosystem-appeng/metric-mcp:your-tag .

# Push to container registry
podman push quay.io/ecosystem-appeng/metric-mcp:your-tag
```

**Using Docker:**
```bash
# Navigate to the metric_ui directory (important for build context)
cd metric_ui

# Build for linux/amd64 platform
docker buildx build --platform linux/amd64 \
  -f api/Dockerfile \
  -t quay.io/ecosystem-appeng/metric-mcp:your-tag .

# Push to container registry
docker push quay.io/ecosystem-appeng/metric-mcp:your-tag
```

#### **Build Streamlit UI (metric-ui)**

```bash
# Build UI container
cd metric_ui
podman buildx build --platform linux/amd64 \
  -f ui/Dockerfile \
  -t quay.io/ecosystem-appeng/metric-ui:your-tag .

# Push to registry
podman push quay.io/ecosystem-appeng/metric-ui:your-tag
```

#### **Build Alerting Service (metric-alerting)**

```bash
# Build alerting container
cd metric_ui
podman buildx build --platform linux/amd64 \
  -f alerting/Dockerfile \
  -t quay.io/ecosystem-appeng/metric-alerting:your-tag .

# Push to registry
podman push quay.io/ecosystem-appeng/metric-alerting:your-tag
```

### Deploy to Kubernetes

After building and pushing the images:

1. **Update Helm values** with your new image tags:
   ```yaml
   # deploy/helm/metric-mcp/values.yaml
   image:
     repository: quay.io/ecosystem-appeng/metric-mcp
     tag: your-tag
   ```

2. **Deploy using Helm**:
   ```bash
   cd deploy/helm
   make install-metric-mcp NAMESPACE=your-namespace
   ```

### Automated CI/CD Build

The project includes GitHub Actions workflow (`.github/workflows/build-and-push.yml`) that automatically builds and pushes container images when changes are pushed to the repository.

**Image naming convention:** `v1.0.{GITHUB_RUN_NUMBER}`

---

## Local Development via Port-Forwarding

In order to develop locally faster on the MCP/UI you can leverage port-forwarding to Llamastack, llm-service and Thanos by making use of `scripts/local-dev.sh` script.

**Pre-requisites**:
1. You have a deployment on the cluster already.
2. You are logged into the cluster and can execute `oc` commands against the cluster.

### Running script
To perform local setup using the `./scripts/local-dev.sh` script, execute the following steps:
1. **Make sure you are logged into the cluster and can execute `oc` commands against the cluster.**
2. Install `uv` by following instructions on the [uv website](https://github.com/astral-sh/uv)
3. Sync up the environment and development dependencies using `uv` in the base directory:
```bash
uv sync --group dev
```
   The `uv sync` command performs the following tasks:
   - Find or download an appropriate Python version
   - Create a virtual environment in `.venv` folder
   - Build complete dependency using `pyproject.toml` (and `uv.lock`) file(s)
   - Sync up project dependencies in the virtual environment
   
4. Activate the virtual environment:
```bash
source .venv/bin/activate
```
5. Export the namespace where the kickstart is deployed:
```sh
export LLM_NAMESPACE=<DESIRED_NAMESPACE>
```
6. Run the script by executing the following command:
```bash
./scripts/local-dev.sh
```

The output should look like this:
![Command Output](docs/img/local-dev-expected.png)

#### Macos weasyprint install

**Still verifying whether we need this setup or not as weasyprint is installed using `uv` in previous step.**

In order to run the mcp locally you'll need to install weasyprint:
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
uv run pytest -v --cov=metric_ui --cov-report=html --cov-report=term

# Run only MCP tests
uv run pytest -v tests/mcp/

# Run specific test file
uv run pytest -v tests/mcp/test_api_endpoints.py
```

To view a detailed coverage report after generating, open `htmlcov/index.html`.

---

## Powered By

- [OpenShift AI](https://www.redhat.com/en/technologies/cloud-computing/openshift/openshift-ai)
- [Prometheus](https://prometheus.io/)
- [Streamlit](https://streamlit.io/)

---

## Feedback & Contributions

We welcome contributions and feedback!  
Please open issues or submit PRs to improve this dashboard or expand model compatibility.

---
