FROM ubuntu:22.04

LABEL maintainer="hakxcore"
LABEL description="Gathm Enterprise - AI Agent Tool Framework"
LABEL version="3.0.0"

# Avoid interactive prompts during install
ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt-get update && apt-get install -y \
    bash \
    curl \
    wget \
    jq \
    python3 \
    python3-pip \
    openssl \
    telnet \
    dnsutils \
    iproute2 \
    net-tools \
    libxml2-utils \
    jp2a \
    dialog \
    pv \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies for API server
RUN pip3 install --no-cache-dir pyyaml

# Create app directory
WORKDIR /opt/gathm

# Copy entire project
COPY . .

# Make all scripts executable
RUN chmod +x gathm agent/*.sh && \
    find tools -type f -name "*.sh" -exec chmod +x {} \; && \
    for dir in tools/*/; do \
        tool_name=$(basename "$dir"); \
        if [ -f "$dir/$tool_name" ]; then \
            chmod +x "$dir/$tool_name"; \
        fi; \
    done

# Create symlinks for easy access
RUN ln -sf /opt/gathm/agent/orchestrator.sh /usr/local/bin/gathm-agent && \
    ln -sf /opt/gathm/gathm /usr/local/bin/gathm

# Create data directories
RUN mkdir -p /root/.gathm/{logs,health,agent/plans}

# Environment
ENV GATHM_ROOT=/opt/gathm
ENV GATHM_LOG_LEVEL=INFO
ENV PATH="/opt/gathm:${PATH}"

# Expose API port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD bash -c "gathm-agent health all --json" || exit 1

# Default: run the API server
CMD ["python3", "/opt/gathm/api/server.py", "--port", "8080"]
