# Enhanced Observability Service for PoC
# Integrates with Korrel8r, Prometheus, and Tempo

import asyncio
import aiohttp
import os
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import json

class PoCObservabilityService:
    def __init__(self):
        self.korrel8r_url = os.getenv("KORREL8R_URL", "http://korrel8r:8080")
        self.prometheus_url = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
        self.tempo_url = os.getenv("TEMPO_URL", "http://tempo:3200")
        
    async def correlate_metric_to_traces(self, metric_query: str, time_range: str = "1h") -> Dict:
        """
        Demonstrate correlation from Prometheus metrics to Tempo traces
        """
        try:
            async with aiohttp.ClientSession() as session:
                # 1. Query Korrel8r for correlations
                correlations = await self._query_korrel8r(
                    session, 
                    start_signal=f"metric:{metric_query}",
                    goal_type="trace"
                )
                
                if not correlations:
                    return {"error": "No correlations found"}
                
                metric_data = await self._query_prometheus(
                    session, metric_query, time_range
                )
                
                traces = []
                for correlation in correlations.get("results", []):
                    trace_id = correlation.get("trace_id")
                    if trace_id:
                        trace_data = await self._get_tempo_trace(session, trace_id)
                        if trace_data:
                            traces.append(trace_data)
                
                return {
                    "metric_query": metric_query,
                    "metric_data": metric_data,
                    "correlations": correlations,
                    "related_traces": traces,
                    "analysis_timestamp": datetime.now().isoformat()
                }
                
        except Exception as e:
            return {"error": f"Correlation failed: {str(e)}"}
    
    async def correlate_trace_to_metrics(self, trace_id: str) -> Dict:
        """
        Demonstrate correlation from Tempo trace to Prometheus metrics
        """
        try:
            async with aiohttp.ClientSession() as session:
                # 1. Query Korrel8r for correlations
                correlations = await self._query_korrel8r(
                    session,
                    start_signal=f'trace:{{.trace_id="{trace_id}"}}',
                    goal_type="metric"
                )
                
                if not correlations:
                    return {"error": "No correlations found"}
                
                # 2. Fetch the original trace data
                trace_data = await self._get_tempo_trace(session, trace_id)
                
                # 3. Fetch related metrics from Prometheus
                metrics = []
                for correlation in correlations.get("results", []):
                    metric_name = correlation.get("metric_name")
                    if metric_name:
                        metric_data = await self._query_prometheus(
                            session, metric_name, "1h"
                        )
                        if metric_data:
                            metrics.append({
                                "metric_name": metric_name,
                                "data": metric_data,
                                "correlation_rule": correlation.get("rule")
                            })
                
                return {
                    "trace_id": trace_id,
                    "trace_data": trace_data,
                    "correlations": correlations,
                    "related_metrics": metrics,
                    "analysis_timestamp": datetime.now().isoformat()
                }
                
        except Exception as e:
            return {"error": f"Correlation failed: {str(e)}"}
    
    async def analyze_incident(self, description: str, time_range: str = "1h") -> Dict:
        """
        AI-powered incident analysis using correlations
        This is a simplified version for the PoC
        """
        try:
            # For PoC, we'll simulate intelligent analysis
            # In full implementation, this would use LLM + real correlations
            
            # Extract potential metrics/services from description
            keywords = self._extract_keywords(description)
            
            correlations = []
            for keyword in keywords:
                # Try to find correlations for each keyword
                if "cpu" in keyword.lower():
                    metric_query = "cpu_usage_percent"
                elif "memory" in keyword.lower():
                    metric_query = "memory_usage_bytes"
                elif "latency" in keyword.lower():
                    metric_query = "http_request_duration_seconds"
                else:
                    continue
                    
                correlation_result = await self.correlate_metric_to_traces(
                    metric_query, time_range
                )
                if not correlation_result.get("error"):
                    correlations.append(correlation_result)
            
            # (simplified for PoC)
            insights = self._generate_insights(description, correlations)
            
            return {
                "incident_description": description,
                "extracted_keywords": keywords,
                "correlations": correlations,
                "ai_insights": insights,
                "analysis_timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            return {"error": f"Incident analysis failed: {str(e)}"}
    
    async def _query_korrel8r(self, session: aiohttp.ClientSession, start_signal: str, goal_type: str) -> Optional[Dict]:
        """Query Korrel8r correlation engine"""
        try:
            payload = {
                "goals": [goal_type],
                "start": {
                    "class": "metric:prometheus",
                    "queries": [start_signal],
                    "constraint": {
                        "limit": 100,
                        "timeout": "30s"
                    }
                }
            }
            
            async with session.post(
                f"{self.korrel8r_url}/api/v1alpha1/graphs/goals",
                json=payload,
                timeout=30
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    print(f"Korrel8r query failed: {response.status}")
                    return None
                    
        except Exception as e:
            print(f"Error querying Korrel8r: {e}")
            return None
    
    async def _query_prometheus(self, session: aiohttp.ClientSession, query: str, time_range: str) -> Optional[Dict]:
        """Query Prometheus for metrics"""
        try:
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=1 if time_range == "1h" else 24)
            
            params = {
                "query": query,
                "start": start_time.isoformat() + "Z",
                "end": end_time.isoformat() + "Z",
                "step": "30s"
            }
            
            async with session.get(
                f"{self.prometheus_url}/api/v1/query_range",
                params=params,
                timeout=30
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    print(f"Prometheus query failed: {response.status}")
                    return None
                    
        except Exception as e:
            print(f"Error querying Prometheus: {e}")
            return None
    
    async def _get_tempo_trace(self, session: aiohttp.ClientSession, trace_id: str) -> Optional[Dict]:
        """Fetch trace data from Tempo"""
        try:
            async with session.get(
                f"{self.tempo_url}/api/traces/{trace_id}",
                timeout=30
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    print(f"Tempo query failed: {response.status}")
                    return None
                    
        except Exception as e:
            print(f"Error querying Tempo: {e}")
            return None
    
    def _extract_keywords(self, description: str) -> List[str]:
        """Extract relevant keywords from incident description"""
        # Simple keyword extraction for PoC
        keywords = []
        words = description.lower().split()
        
        relevant_terms = [
            "cpu", "memory", "latency", "error", "timeout", 
            "slow", "high", "spike", "service", "database"
        ]
        
        for word in words:
            if word in relevant_terms:
                keywords.append(word)
                
        return list(set(keywords))
    
    def _generate_insights(self, description: str, correlations: List[Dict]) -> Dict:
        """Generate AI insights from correlations (simplified for PoC)"""
        
        # Count correlation patterns
        total_correlations = sum(len(c.get("correlations", {}).get("results", [])) for c in correlations)
        
        # Simple rule-based insights for PoC
        insights = {
            "summary": f"Analyzed incident with {len(correlations)} metric patterns and {total_correlations} total correlations.",
            "potential_causes": [],
            "recommendations": [],
            "severity": "medium"
        }
        
        # Add insights based on keywords in description
        if "cpu" in description.lower():
            insights["potential_causes"].append("High CPU utilization detected")
            insights["recommendations"].append("Consider scaling horizontally or optimizing CPU-intensive operations")
        
        if "memory" in description.lower():
            insights["potential_causes"].append("Memory pressure identified")
            insights["recommendations"].append("Check for memory leaks or increase memory allocation")
        
        if "latency" in description.lower() or "slow" in description.lower():
            insights["potential_causes"].append("Performance degradation observed")
            insights["recommendations"].append("Investigate bottlenecks in critical path")
        
        # Determine severity
        if total_correlations > 10:
            insights["severity"] = "high"
        elif total_correlations > 5:
            insights["severity"] = "medium"
        else:
            insights["severity"] = "low"
        
        return insights

# FastAPI integration for PoC
if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    
    app = FastAPI(title="PoC Observability Service")
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    service = PoCObservabilityService()
    
    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "service": "poc-observability"}
    
    @app.post("/correlate/metric-to-traces")
    async def metric_to_traces(payload: dict):
        return await service.correlate_metric_to_traces(
            payload.get("metric_query"),
            payload.get("time_range", "1h")
        )
    
    @app.post("/correlate/trace-to-metrics")
    async def trace_to_metrics(payload: dict):
        return await service.correlate_trace_to_metrics(payload.get("trace_id"))
    
    @app.post("/analyze/incident")
    async def analyze_incident(payload: dict):
        return await service.analyze_incident(
            payload.get("description"),
            payload.get("time_range", "1h")
        )
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
