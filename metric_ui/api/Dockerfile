FROM registry.access.redhat.com/ubi9/python-311:1-38
WORKDIR /app

USER root


RUN dnf install -y \
    fontconfig \
    harfbuzz \
    pango \
    cairo \
    libxml2 \
    libxslt \
    freetype \
    openjpeg2 \
    python3-devel \
    gcc \
    gcc-c++ \
    libffi-devel \
    dejavu-sans-fonts \
    && dnf clean all


USER 1001


COPY api/mcp.py .
COPY api/requirements.txt .
COPY api/report_assets/ report_assets/
COPY core/ core/
RUN pip install --no-cache-dir -r requirements.txt
CMD ["uvicorn", "mcp:app", "--host", "0.0.0.0", "--port", "8000"]