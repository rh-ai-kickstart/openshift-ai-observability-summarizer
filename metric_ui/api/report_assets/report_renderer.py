"""
Report Renderer Module
Handles report generation for different formats with proper separation of concerns.
"""

import os
import markdown
from typing import List, Dict, Any, Optional
import report_assets.report_config as report_config

# Optional weasyprint import for PDF generation
try:
    from weasyprint import HTML, CSS
    from weasyprint.text.fonts import FontConfiguration
    WEASYPRINT_AVAILABLE = True
except Exception as e:
    WEASYPRINT_AVAILABLE = False
    print("WeasyPrint not available. PDF generation will be disabled.")

# Import models from mcp module
from pydantic import BaseModel


class MetricCard(BaseModel):
    name: str
    avg: Optional[float]
    max: Optional[float]
    values: List[Dict[str, Any]]


class ReportSchema(BaseModel):
    generated_at: str
    model_name: str
    start_date: str
    end_date: str
    summarize_model_id: str
    summary: str
    metrics: List[MetricCard]
    trend_chart_image: Optional[str] = None


def load_report_css() -> str:
    """Load CSS from external file for better separation of concerns"""
    css_path = os.path.join(os.path.dirname(__file__), "report.css")
    try:
        with open(css_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        # Fallback to minimal CSS if file not found
        return """
        body { font-family: Arial, sans-serif; margin: 40px; }
        .header { background-color: #e7f3ff; padding: 20px; border-radius: 5px; margin-bottom: 30px; }
        .section { margin-bottom: 30px; }
        .summary { background-color: #f1f1f1; padding: 24px; border-radius: 7px; }
        .dashboard { display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; }
        .metric-card { background-color: #f1f1f1; padding: 1em; border-radius: 12px; }
        table { width: 100%; border-collapse: collapse; }
        th, td { border: 1px solid #ddd; padding: 8px; }
        """


class ReportRenderer:
    """Handles report generation for different formats with proper separation of concerns"""

    def __init__(self, report_schema: ReportSchema):
        self.schema = report_schema
        self.summary_html = markdown.markdown(
            report_schema.summary, extensions=["extra"]
        )
        self.css = load_report_css()
        self.config = report_config.REPORT_CONFIG

    def render_html(self) -> str:
        """Generate complete HTML report using external templates"""
        return report_config.HTML_TEMPLATE.format(
            title=self.config["title"],
            css=self.css,
            header=self._render_header(),
            report_details=self._render_report_details(),
            summary=self._render_summary(),
            metrics_dashboard=self._render_metrics_dashboard(),
            trend_chart=self._render_trend_chart(),
        )

    def render_markdown(self) -> str:
        """Generate Markdown report using external templates"""
        return report_config.MARKDOWN_TEMPLATE.format(
            generated_at=self.schema.generated_at,
            report_details_table=self._render_markdown_table(),
            summary=self.schema.summary.strip(),
            metrics_table=self._render_markdown_metrics(),
            trend_chart=self._render_markdown_chart(),
        )

    def _render_header(self) -> str:
        """Render report header section using template"""
        return report_config.SECTION_TEMPLATES["header"].format(
            generated_at=self.schema.generated_at
        )

    def _render_report_details(self) -> str:
        """Render report details table using template"""
        return report_config.SECTION_TEMPLATES["report_details"].format(
            model_name=self.schema.model_name,
            date_range=f"{self.schema.start_date} to {self.schema.end_date}",
            summarize_model=self.schema.summarize_model_id,
        )

    def _render_summary(self) -> str:
        """Render model insights summary using template"""
        return report_config.SECTION_TEMPLATES["summary"].format(
            summary_html=self.summary_html
        )

    def _render_metrics_dashboard(self) -> str:
        """Render metrics dashboard with cards using templates"""
        metric_cards = []
        for metric in self.schema.metrics:
            metric_cards.append(self._render_metric_card(metric))

        return report_config.SECTION_TEMPLATES["metrics_dashboard"].format(
            metric_cards="".join(metric_cards)
        )

    def _render_metric_card(self, metric: MetricCard) -> str:
        """Render individual metric card using templates"""
        if metric.avg is not None and metric.max is not None:
            return report_config.METRIC_CARD_TEMPLATES["with_data"].format(
                name=metric.name, avg=metric.avg, max=metric.max
            )
        else:
            return report_config.METRIC_CARD_TEMPLATES["no_data"].format(
                name=metric.name
            )

    def _render_trend_chart(self) -> str:
        """Render trend chart section using template"""
        chart_content = ""
        if self.schema.trend_chart_image:
            chart_content = f'<img src="data:image/png;base64,{self.schema.trend_chart_image}" alt="Trend Over Time Chart"/>'

        return report_config.SECTION_TEMPLATES["trend_chart"].format(
            chart_content=chart_content
        )

    def _render_markdown_table(self) -> str:
        """Render markdown table for report details using template"""

        def escape_markdown_table_content(text: str) -> str:
            return text.replace("|", "\\|").replace("\n", " ")

        escaped_model_name = escape_markdown_table_content(self.schema.model_name)
        escaped_summarize_model = escape_markdown_table_content(
            self.schema.summarize_model_id
        )
        escaped_date_range = escape_markdown_table_content(
            f"{self.schema.start_date} to {self.schema.end_date}"
        )

        return report_config.MARKDOWN_TEMPLATES["report_details"].format(
            model_name=escaped_model_name,
            date_range=escaped_date_range,
            summarize_model=escaped_summarize_model,
        )

    def _render_markdown_metrics(self) -> str:
        """Render markdown metrics table using templates"""

        def escape_markdown_table_content(text: str) -> str:
            return text.replace("|", "\\|").replace("\n", " ")

        table_rows = [report_config.MARKDOWN_TEMPLATES["metrics_header"]]

        for metric in self.schema.metrics:
            escaped_metric_name = escape_markdown_table_content(metric.name)
            if metric.avg is not None and metric.max is not None:
                table_rows.append(
                    report_config.MARKDOWN_TEMPLATES["metric_row"].format(
                        metric_name=escaped_metric_name,
                        avg_value=f"{metric.avg:.2f}",
                        max_value=f"{metric.max:.2f}",
                    )
                )
            else:
                table_rows.append(
                    report_config.MARKDOWN_TEMPLATES["metric_row"].format(
                        metric_name=escaped_metric_name,
                        avg_value="N/A",
                        max_value="N/A",
                    )
                )

        return "\n".join(table_rows)

    def _render_markdown_chart(self) -> str:
        """Render markdown chart section"""
        if self.schema.trend_chart_image:
            return (
                f"![Trend Chart](data:image/png;base64,{self.schema.trend_chart_image})"
            )
        return ""


def generate_html_report(report_schema: ReportSchema) -> str:
    """Generate HTML report from unified schema"""
    renderer = ReportRenderer(report_schema)
    return renderer.render_html()


def generate_markdown_report(report_schema: ReportSchema) -> str:
    """Generate Markdown report from unified schema"""
    renderer = ReportRenderer(report_schema)
    return renderer.render_markdown()


def generate_pdf_report(report_schema: ReportSchema) -> bytes:
    """Generate PDF report from unified schema"""
    
    if not WEASYPRINT_AVAILABLE:
        # Fallback to HTML when weasyprint is not available
        html_content = generate_html_report(report_schema)
        print("WeasyPrint not available. Returning HTML content instead of PDF.")
        return html_content.encode("utf-8")

    try:
        html_content = generate_html_report(report_schema)
        css_string = load_report_css()
        font_config = FontConfiguration()

        # Use absolute path for base_url
        base_dir = os.path.dirname(os.path.abspath(__file__))
        html = HTML(string=html_content, base_url=base_dir)
        css = CSS(string=css_string)

        print("Generating PDF with WeasyPrint...")
        pdf_bytes = html.write_pdf(stylesheets=[css], font_config=font_config)
        print("PDF generated successfully!")
        return pdf_bytes
    except Exception as e:
        print(f"Error generating PDF: {e}")
        import traceback
        traceback.print_exc()
        
        # Return HTML as fallback
        html_content = generate_html_report(report_schema)
        return html_content.encode("utf-8")
