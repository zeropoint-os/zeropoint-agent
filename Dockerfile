FROM golang:1.25-bookworm AS base

# Install system dependencies for development
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    wget \
    vim \
    nano \
    htop \
    jq \
    tree \
    net-tools \
    gnupg \
    lsb-release \
    netcat-traditional \
    libsystemd-dev \
    gdisk \
    parted \
    kpartx \
    e2fsprogs \
    xfsprogs \
    lvm2 \
    cryptsetup \
    util-linux \
    udev \
    kmod \
    lsof \
    psmisc \
    dmsetup \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js (needed for OpenAPI generator)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs

# Install Java (required for OpenAPI generator)
RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*

# Install Go development tools (pinned, built with Go 1.25)
# gopls requires Go >=1.25; pin to a released version and install with Go 1.25.
RUN go install golang.org/x/tools/gopls@v0.21.0
RUN go install github.com/go-delve/delve/cmd/dlv@v1.25.2

# Install Swag tool for generating Swagger docs
RUN go install github.com/swaggo/swag/cmd/swag@latest

# Install OpenAPI Generator CLI and swagger2openapi converter
RUN npm install -g @openapitools/openapi-generator-cli swagger2openapi js-yaml
# Make npm global modules writable for all users
RUN chmod -R a+rwx /usr/lib/node_modules/@openapitools /usr/lib/node_modules/swagger2openapi /usr/lib/node_modules/js-yaml
# Initialize OpenAPI generator with specific version
RUN npx @openapitools/openapi-generator-cli version-manager set 7.0.1

# Ensure the /go directory is writable for all users (for development purposes)
RUN chmod -R a+rwx /go

# Configure git to trust mounted workspace directories (needed for Docker bind mounts)
RUN git config --global --add safe.directory '*'

# Ensure Go binaries installed into /go/bin are available on PATH
ENV PATH="/go/bin:${PATH}"

# Install Terraform CLI
RUN curl -fsSL https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" > /etc/apt/sources.list.d/hashicorp.list && \
    apt-get update && \
    apt-get install -y terraform && \
    rm -rf /var/lib/apt/lists/* &&\
    terraform init

# Install wscat for WebSocket testing
RUN npm -g install wscat

# Install nvidia-container-runtime for GPU support in Docker
RUN apt-get update && apt-get install -y --no-install-recommends \
   curl \
   gnupg2

RUN curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg \
  && curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

RUN apt-get update
RUN export NVIDIA_CONTAINER_TOOLKIT_VERSION=1.18.1-1 &&\
  apt-get install -y \
    nvidia-container-toolkit=${NVIDIA_CONTAINER_TOOLKIT_VERSION} \
    nvidia-container-toolkit-base=${NVIDIA_CONTAINER_TOOLKIT_VERSION} \
    libnvidia-container-tools=${NVIDIA_CONTAINER_TOOLKIT_VERSION} \
    libnvidia-container1=${NVIDIA_CONTAINER_TOOLKIT_VERSION}

RUN nvidia-ctk runtime configure --runtime=docker

RUN mkdir -p /etc/zeropoint