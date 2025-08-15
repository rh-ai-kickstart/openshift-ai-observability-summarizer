FROM registry.access.redhat.com/ubi9/python-311:latest

USER 0
RUN ln -sf /usr/bin/python3.11 /usr/bin/python \
    && ln -sf /usr/bin/pip3.11 /usr/bin/pip \
    && chmod -R g+rwX /opt/app-root \
    && chgrp -R 0 /opt/app-root

WORKDIR /app

RUN python --version \
 && python -c "import sys; print('Python version:', sys.version)"

COPY mcp-server/requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip \
 && python -m pip install --no-cache-dir -r requirements.txt

COPY core/ core/
COPY mcp_server/ mcp_server/

RUN chmod -R g+rwX /app && chgrp -R 0 /app
USER 1001

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "mcp_server.api:app", "--host", "0.0.0.0", "--port", "8080"]


