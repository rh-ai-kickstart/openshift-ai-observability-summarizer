#!/bin/bash

# AI Observability Metric Summarizer - Local Development Script

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PROMETHEUS_NAMESPACE="openshift-monitoring"
LLM_NAMESPACE="${LLM_NAMESPACE:-m3}"
THANOS_PORT=9090
LLAMASTACK_PORT=8321
LLAMA_MODEL_PORT=8080
MCP_PORT=8000
UI_PORT=8501

echo -e "${BLUE}🚀 AI Observability Metric Summarizer - Local Development Setup${NC}"
echo "=============================================================="

# Function to cleanup on exit
cleanup() {
    echo -e "\n${YELLOW}🧹 Cleaning up services and port-forwards...${NC}"
    pkill -f "oc port-forward" || true
    pkill -f "uvicorn mcp:app" || true
    pkill -f "streamlit run ui.py" || true
    echo -e "${GREEN}✅ Cleanup complete${NC}"
}
trap cleanup EXIT INT TERM

# Function to check if service exists
check_prerequisites() {
    echo -e "${BLUE}🔍 Checking prerequisites...${NC}"
    
    if ! command -v oc &> /dev/null; then
        echo -e "${RED}❌ OpenShift CLI (oc) is not installed${NC}"
        exit 1
    fi
    
    if ! oc whoami &> /dev/null; then
        echo -e "${RED}❌ Not logged in to OpenShift cluster${NC}"
        echo -e "${YELLOW}   Please run: oc login${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}✅ Prerequisites check passed${NC}"
}

# Function to find and start port forwards
start_port_forwards() {
    echo -e "${BLUE}🔍 Finding pods and starting port-forwards...${NC}"
    
    # Find Thanos pod
    THANOS_POD=$(oc get pods -n "$PROMETHEUS_NAMESPACE" -o name | grep thanos-querier | head -1 | cut -d'/' -f2 || echo "")
    if [ -z "$THANOS_POD" ]; then
        THANOS_POD=$(oc get pods -n "$PROMETHEUS_NAMESPACE" -o name | grep prometheus | head -1 | cut -d'/' -f2 || echo "")
    fi
    
    if [ -n "$THANOS_POD" ]; then
        echo -e "${GREEN}✅ Found Thanos pod: $THANOS_POD${NC}"
        oc port-forward pod/"$THANOS_POD" "$THANOS_PORT:9090" -n "$PROMETHEUS_NAMESPACE" &
        echo -e "${GREEN}   📊 Thanos available at: http://localhost:$THANOS_PORT${NC}"
    else
        echo -e "${RED}❌ No Thanos/Prometheus pod found${NC}"
        exit 1
    fi
    
    # Find LlamaStack pod
    LLAMASTACK_POD=$(oc get pods -n "$LLM_NAMESPACE" -o name | grep -E "(llama-stack|llamastack)" | head -1 | cut -d'/' -f2 || echo "")
    if [ -n "$LLAMASTACK_POD" ]; then
        echo -e "${GREEN}✅ Found LlamaStack pod: $LLAMASTACK_POD${NC}"
        oc port-forward pod/"$LLAMASTACK_POD" "$LLAMASTACK_PORT:8321" -n "$LLM_NAMESPACE" &
        echo -e "${GREEN}   🦙 LlamaStack available at: http://localhost:$LLAMASTACK_PORT${NC}"
    else
        echo -e "${YELLOW}⚠️  LlamaStack pod not found${NC}"
    fi
    
    # Find Llama Model pod
    LLAMA_MODEL_POD=$(oc get pods -n "$LLM_NAMESPACE" -o name | grep -E "(llama-3|inference)" | grep -v stack | head -1 | cut -d'/' -f2 || echo "")
    if [ -n "$LLAMA_MODEL_POD" ]; then
        echo -e "${GREEN}✅ Found Llama Model pod: $LLAMA_MODEL_POD${NC}"
        oc port-forward pod/"$LLAMA_MODEL_POD" "$LLAMA_MODEL_PORT:8080" -n "$LLM_NAMESPACE" &
        echo -e "${GREEN}   🤖 Llama Model available at: http://localhost:$LLAMA_MODEL_PORT${NC}"
    else
        echo -e "${YELLOW}⚠️  Llama Model pod not found${NC}"
    fi
    
    sleep 3  # Give port-forwards time to establish
}

# Function to start local services
start_local_services() {
    echo -e "${BLUE}🏃 Starting local services...${NC}"
    
    # Get service account token
    TOKEN=$(oc whoami -t)
    
    # Set environment variables
    export PROMETHEUS_URL="http://localhost:$THANOS_PORT"
    export LLAMA_STACK_URL="http://localhost:$LLAMASTACK_PORT/v1/openai/v1"
    export THANOS_TOKEN="$TOKEN"
    export MODEL_CONFIG=$(cat scripts/model-config.json | jq -c .)
    export MCP_API_URL="http://localhost:$MCP_PORT"
    export PROM_URL="http://localhost:$THANOS_PORT"
    export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib:$DYLD_FALLBACK_LIBRARY_PATH"
    
    # macOS weasyprint support
    export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib:$DYLD_FALLBACK_LIBRARY_PATH"
    
    # Start MCP service
echo -e "${BLUE}🔧 Starting MCP backend...${NC}"
(cd src/api && python3 -m uvicorn metrics_api:app --host 0.0.0.0 --port $MCP_PORT --reload > log.txt) &
    MCP_PID=$!
    
    # Wait for MCP to start
    sleep 3
    
    # Test MCP service
    if curl -s --connect-timeout 5 "http://localhost:$MCP_PORT/models" > /dev/null; then
        echo -e "${GREEN}✅ MCP backend started successfully${NC}"
    else
        echo -e "${RED}❌ MCP backend failed to start${NC}"
        exit 1
    fi
    
    # Start Streamlit UI
    echo -e "${BLUE}🎨 Starting Streamlit UI...${NC}"
    (cd src/ui && streamlit run ui.py --server.port $UI_PORT --server.address 0.0.0.0 --server.headless true) &
    UI_PID=$!
    
    # Wait for UI to start
    sleep 5
    
    echo -e "${GREEN}✅ All services started successfully!${NC}"
}

# Main execution
main() {
    check_prerequisites
    echo ""
    echo -e "${BLUE}--------------------------------${NC}"
    echo -e "${BLUE}Namespace being used for setup -> LLM_NAMESPACE: $LLM_NAMESPACE${NC}"
    echo -e "${BLUE}--------------------------------${NC}\n"

    start_port_forwards
    start_local_services
    
    echo -e "\n${GREEN}🎉 Setup complete! All services are running.${NC}"
    echo -e "\n${BLUE}📋 Services Available:${NC}"
    echo -e "   ${YELLOW}🎨 Streamlit UI: http://localhost:$UI_PORT${NC}"
    echo -e "   ${YELLOW}🔧 MCP Backend API: http://localhost:$MCP_PORT/docs${NC}"
    echo -e "   ${YELLOW}📊 Prometheus: http://localhost:$THANOS_PORT${NC}"
    echo -e "   ${YELLOW}🦙 LlamaStack: http://localhost:$LLAMASTACK_PORT${NC}"
    echo -e "   ${YELLOW}🤖 Llama Model: http://localhost:$LLAMA_MODEL_PORT${NC}"
    
    echo -e "\n${GREEN}🎯 Ready to use! Open your browser to http://localhost:$UI_PORT${NC}"
    echo -e "\n${YELLOW}📝 Note: Keep this terminal open to maintain all services${NC}"
    echo -e "${YELLOW}Press Ctrl+C to stop all services and cleanup${NC}"
    
    # Keep script running
    wait
}

# Run main function
main 