# LLM Client for Observability Analysis
import aiohttp
import json
import os
from typing import Dict, List, Optional

class LLMClient:
    def __init__(self):
        self.llm_endpoint = os.getenv("LLM_ENDPOINT", "http://llm-d-inference-gateway-istio.llm-d.svc.cluster.local:80")
        self.model_name = os.getenv("LLM_MODEL", "llama-3.2-3b-instruct")
        self.max_tokens = int(os.getenv("LLM_MAX_TOKENS", "4096"))
        self.timeout = int(os.getenv("LLM_TIMEOUT", "60"))
    
    async def analyze_correlations(self, correlations: Dict, incident_description: str = "") -> Dict:
        """
        Use LLM to analyze correlation results and provide insights
        """
        try:
            # Build context from correlation data
            context = self._build_correlation_context(correlations, incident_description)
            
            # Create analysis prompt
            prompt = self._create_analysis_prompt(context)
            
            # Query LLM
            analysis = await self._query_llm(prompt)
            
            return {
                "llm_analysis": analysis,
                "correlation_summary": context,
                "recommendations": self._extract_recommendations(analysis)
            }
            
        except Exception as e:
            return {
                "error": f"LLM analysis failed: {str(e)}",
                "fallback_analysis": self._fallback_analysis(correlations)
            }
    
    async def generate_incident_insights(self, description: str, metrics: List[Dict], traces: List[Dict]) -> Dict:
        """
        Generate comprehensive incident analysis using LLM
        """
        try:
            context = {
                "incident": description,
                "metrics_summary": self._summarize_metrics(metrics),
                "traces_summary": self._summarize_traces(traces),
                "timeline": self._extract_timeline(metrics, traces)
            }
            
            prompt = self._create_incident_prompt(context)
            
            analysis = await self._query_llm(prompt)
            
            return {
                "root_cause_analysis": analysis.get("root_cause", "Unknown"),
                "impact_assessment": analysis.get("impact", "Unknown"),
                "recommendations": analysis.get("recommendations", []),
                "severity": analysis.get("severity", "medium"),
                "llm_raw_response": analysis
            }
            
        except Exception as e:
            return {"error": f"Incident analysis failed: {str(e)}"}
    
    async def _query_llm(self, prompt: str) -> Dict:
        """
        Query the LLM endpoint with the given prompt
        """
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "model": self.model_name,
                    "prompt": prompt,
                    "max_tokens": self.max_tokens,
                    "temperature": 0.1,  # Low temperature for consistent analysis
                    "stop": ["Human:", "Assistant:"]
                }
                
                headers = {"Content-Type": "application/json"}
                async with session.post(
                    f"{self.llm_endpoint}/v1/completions",
                    json=payload,
                    headers=headers,
                    timeout=self.timeout
                ) as response:
                    
                    if response.status == 200:
                        result = await response.json()
                        return self._parse_llm_response(result)
                    else:
                        raise Exception(f"LLM endpoint returned {response.status}")
                        
        except Exception as e:
            raise Exception(f"LLM query failed: {str(e)}")
    
    def _build_correlation_context(self, correlations: Dict, incident_description: str) -> Dict:
        """Build context summary from correlation results"""
        context = {
            "incident_description": incident_description,
            "total_correlations": 0,
            "correlation_types": {},
            "related_services": set(),
            "time_range": "1h"  # Default
        }
        
        # Process correlation results
        for correlation_result in correlations.get("correlations", []):
            results = correlation_result.get("correlations", {}).get("results", [])
            context["total_correlations"] += len(results)
            
            for result in results:
                # Extract service names and correlation types
                service = result.get("service_name", "unknown")
                rule = result.get("rule", "unknown")
                
                context["related_services"].add(service)
                context["correlation_types"][rule] = context["correlation_types"].get(rule, 0) + 1
        
        context["related_services"] = list(context["related_services"])
        return context
    
    def _create_analysis_prompt(self, context: Dict) -> str:
        """Create a structured prompt for correlation analysis"""
        return f"""You are an expert SRE analyzing observability data for an incident.

INCIDENT DESCRIPTION:
{context.get('incident_description', 'No description provided')}

CORRELATION ANALYSIS:
- Total correlations found: {context.get('total_correlations', 0)}
- Related services: {', '.join(context.get('related_services', []))}
- Correlation types: {context.get('correlation_types', {})}

TASK:
Analyze this correlation data and provide:
1. **Root Cause Analysis**: Most likely cause based on correlations
2. **Impact Assessment**: Severity and scope of the issue
3. **Immediate Actions**: 3 specific steps to take now
4. **Preventive Measures**: How to prevent this in the future

Format your response as JSON with keys: root_cause, impact, immediate_actions, preventive_measures, severity (low/medium/high).

Analysis:"""

    def _create_incident_prompt(self, context: Dict) -> str:
        """Create prompt for comprehensive incident analysis"""
        return f"""You are an expert SRE performing incident analysis using observability data.

INCIDENT REPORT:
{context.get('incident', 'No description')}

METRICS SUMMARY:
{context.get('metrics_summary', 'No metrics data')}

TRACES SUMMARY:
{context.get('traces_summary', 'No traces data')}

TIMELINE:
{context.get('timeline', 'No timeline data')}

Provide a comprehensive analysis in JSON format with these keys:
- root_cause: Primary cause of the issue
- impact: Business and technical impact
- recommendations: List of specific actions
- severity: low/medium/high/critical
- timeline_analysis: Key events and their relationships

Analysis:"""

    def _parse_llm_response(self, llm_result: Dict) -> Dict:
        """Parse and structure LLM response"""
        try:
            # Extract text from LLM response
            text = llm_result.get("choices", [{}])[0].get("text", "")
            
            # Try to parse as JSON
            if text.strip().startswith("{"):
                return json.loads(text.strip())
            else:
                # Fallback: structure the text response
                return {
                    "analysis": text,
                    "structured": False
                }
                
        except (json.JSONDecodeError, KeyError, IndexError):
            return {
                "analysis": str(llm_result),
                "structured": False,
                "error": "Failed to parse LLM response"
            }
    
    def _summarize_metrics(self, metrics: List[Dict]) -> str:
        """Summarize metrics data for LLM context"""
        if not metrics:
            return "No metrics data available"
        
        summary = []
        for metric in metrics[:5]:  # Limit to top 5
            name = metric.get("metric_name", "unknown")
            # Add basic metric info
            summary.append(f"- {name}: Recent activity detected")
        
        return "\n".join(summary)
    
    def _summarize_traces(self, traces: List[Dict]) -> str:
        """Summarize traces data for LLM context"""
        if not traces:
            return "No traces data available"
        
        summary = []
        for trace in traces[:5]:  # Limit to top 5
            trace_id = trace.get("traceID", "unknown")[:16]
            duration = trace.get("duration", 0)
            spans = trace.get("spanCount", 0)
            
            summary.append(f"- Trace {trace_id}: {duration/1000:.1f}ms, {spans} spans")
        
        return "\n".join(summary)
    
    def _extract_timeline(self, metrics: List[Dict], traces: List[Dict]) -> str:
        """Extract timeline from metrics and traces"""
        return "Timeline analysis: Recent activity correlation detected"
    
    def _extract_recommendations(self, analysis: Dict) -> List[str]:
        """Extract actionable recommendations from LLM analysis"""
        if isinstance(analysis, dict):
            return analysis.get("immediate_actions", analysis.get("recommendations", []))
        return ["Check service logs", "Monitor resource usage", "Verify dependencies"]
    
    def _fallback_analysis(self, correlations: Dict) -> Dict:
        """Provide fallback analysis when LLM is unavailable"""
        total_correlations = sum(
            len(c.get("correlations", {}).get("results", [])) 
            for c in correlations.get("correlations", [])
        )
        
        severity = "high" if total_correlations > 10 else "medium" if total_correlations > 5 else "low"
        
        return {
            "root_cause": f"Multiple correlation patterns detected ({total_correlations} correlations)",
            "impact": f"Potential service impact based on correlation count",
            "recommendations": [
                "Investigate high-correlation services first",
                "Check recent deployments or changes",
                "Monitor key metrics for anomalies"
            ],
            "severity": severity,
            "note": "Fallback analysis - LLM unavailable"
        }
