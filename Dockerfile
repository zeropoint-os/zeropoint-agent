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