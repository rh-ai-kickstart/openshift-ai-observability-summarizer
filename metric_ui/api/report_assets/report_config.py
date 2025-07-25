"""
Report Configuration and Templates
This module contains configuration settings and templates for report generation.
"""

# Report Templates
HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
    <style>{css}</style>
</head>
<body>
    {header}
    {report_details}
    {summary}
    {metrics_dashboard}
    {trend_chart}
</body>
</html>"""

MARKDOWN_TEMPLATE = """# 📊 AI Model Metrics Report

**Generated:** {generated_at}

## 📋 Report Details

{report_details_table}

## 🧠 Model Insights Summary

{summary}

## 📊 Metric Dashboard

{metrics_table}

## 📈 Trend Over Time

{trend_chart}"""

# Report Configuration
REPORT_CONFIG = {
    "title": "AI Metrics Report",
    "page_break_avoid": True,
    "chart_width": "100%",
    "chart_height": "auto",
    "metric_grid_columns": 3,
    "date_format": "%Y-%m-%d %H:%M:%S",
    "decimal_places": 2,
}

# Section Templates
SECTION_TEMPLATES = {
    "header": """<div class="header">
    <h1>📊 AI Model Metrics Report</h1>
    <p><strong>Generated:</strong> {generated_at}</p>
</div>""",
    "report_details": """<div class="section no-page-break">
    <h2>📋 Report Details</h2>
    <table>
        <tr><th>Model Selected for Analysis</th><td>{model_name}</td></tr>
        <tr><th>Investment Range (Time Period)</th><td>{date_range}</td></tr>
        <tr><th>Summarize Model Chosen</th><td>{summarize_model}</td></tr>
    </table>
</div>""",
    "summary": """<div class="section">
    <h2>🧠 Model Insights Summary</h2>
    <div class="summary">
        {summary_html}
    </div>
</div>""",
    "metrics_dashboard": """<div class="section no-page-break">
    <h2>📊 Metric Dashboard</h2>
    <div class="dashboard">
{metric_cards}
    </div>
</div>""",
    "trend_chart": """<div class="section no-page-break">
    <h2>📈 Trend Over Time</h2>
    <div class="chart-container">
        {chart_content}
    </div>
</div>""",
}

# Metric Card Templates
METRIC_CARD_TEMPLATES = {
    "with_data": """
        <div class="metric-card">
            <div class="metric-label">{name}</div>
            <div class="metric-value">{avg:.2f}</div>
            <div class="metric-delta">↑ Max: {max:.2f}</div>
        </div>""",
    "no_data": """
        <div class="metric-card">
            <div class="metric-label">{name}</div>
            <div class="metric-value">N/A</div>
            <div class="metric-delta">No data</div>
        </div>""",
}

# Markdown Table Templates
MARKDOWN_TEMPLATES = {
    "report_details": """| Field | Value |
|-------|-------|
| **Model Selected for Analysis** | {model_name} |
| **Investment Range (Time Period)** | {date_range} |
| **Summarize Model Chosen** | {summarize_model} |""",
    "metrics_header": """| Metric | Average Value | Max Value |
|--------|---------------|-----------|""",
    "metric_row": "| {metric_name} | {avg_value} | {max_value} |",
}
