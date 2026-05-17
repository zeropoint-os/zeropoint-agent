FROM python:3.11-slim-bullseye AS develop

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update &&\
    apt-get install -y \
        jq curl wget git netcat-traditional gnupg2 lsb-release ca-certificates &&\
    apt-get clean &&\
    rm -rf /var/lib/apt/lists/*

# Terraform
RUN curl -fsSL https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" > /etc/apt/sources.list.d/hashicorp.list && \
    apt-get update && \
    apt-get install -y terraform && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    terraform init -backend=false -input=false /tmp || true

# nvidia-container-toolkit (for GPU passthrough into module containers via docker-in-docker)
RUN curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg && \
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
        sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
        tee /etc/apt/sources.list.d/nvidia-container-toolkit.list && \
    apt-get update && \
    export NVIDIA_CONTAINER_TOOLKIT_VERSION=1.18.1-1 && \
    apt-get install -y \
        nvidia-container-toolkit=${NVIDIA_CONTAINER_TOOLKIT_VERSION} \
        nvidia-container-toolkit-base=${NVIDIA_CONTAINER_TOOLKIT_VERSION} \
        libnvidia-container-tools=${NVIDIA_CONTAINER_TOOLKIT_VERSION} \
        libnvidia-container1=${NVIDIA_CONTAINER_TOOLKIT_VERSION} && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Configure docker runtime for nvidia (writes /etc/docker/daemon.json, picked up by dind at runtime)
RUN nvidia-ctk runtime configure --runtime=docker

COPY ./requirements.txt /tmp/pip-tmp/
RUN pip install --no-cache-dir -r /tmp/pip-tmp/requirements.txt \
    && rm -rf /tmp/pip-tmp

FROM develop as runtime

WORKDIR /app

COPY . /app

EXPOSE 2370
CMD ["python", "-m", "zeropoint_agent", "serve"]