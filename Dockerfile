# Multi-stage build for smaller image
# Supports: linux/amd64, linux/arm64, linux/arm/v7
FROM ubuntu:22.04 AS base

LABEL maintainer="hakxcore"
LABEL description="Gathm Enterprise - AI Agent Tool Framework"
LABEL version="3.0.0"

# Avoid interactive prompts during install
ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies (works on all Ubuntu architectures: amd64, arm64, armv7)
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    wget \
    jq \
    python3 \
    python3-pip \
    openssl \
    dnsutils \
    iproute2 \
    net-tools \
    libxml2-utils \
    pv \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Install Python dependencies for API server (optional - graceful fallback)
RUN pip3 install --no-cache-dir pyyaml 2>/dev/null || true

# Create non-root user for security
ARG GATHM_USER=gathm
ARG GATHM_UID=1000
ARG GATHM_GID=1000
RUN groupadd -g ${GATHM_GID} ${GATHM_USER} 2>/dev/null || true && \
    useradd -m -u ${GATHM_UID} -g ${GATHM_GID} -s /bin/bash ${GATHM_USER} 2>/dev/null || true

# Create app directory
WORKDIR /opt/gathm

# Copy entire project
COPY --chown=${GATHM_USER}:${GATHM_USER} . .

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
RUN ln -sf /opt/gathm/gathm /usr/local/bin/gathm && \
    ln -sf /opt/gathm/agent/orchestrator.sh /usr/local/bin/gathm-agent

# Create data directories owned by the non-root user
RUN mkdir -p /home/${GATHM_USER}/.gathm/{logs,health,agent/plans} && \
    chown -R ${GATHM_USER}:${GATHM_USER} /home/${GATHM_USER}/.gathm

# Switch to non-root user
USER ${GATHM_USER}

# Environment
ENV GATHM_ROOT=/opt/gathm
ENV GATHM_LOG_LEVEL=INFO
ENV PATH="/opt/gathm:${PATH}"
ENV HOME=/home/${GATHM_USER}

# Expose API port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD bash -c "gathm health all --json" || exit 1

# Default: run the API server
CMD ["python3", "/opt/gathm/api/server.py", "--port", "8080"]
