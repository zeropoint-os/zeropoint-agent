#!/bin/bash
# Generate client libraries from OpenAPI spec

set -e

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ARTIFACTS_DIR=${ARTIFACTS_DIR:-"$SCRIPT_DIR/../artifacts"}
CLIENTS_DIR="$ARTIFACTS_DIR/clients"

echo "Generating client libraries..."

# Check if OpenAPI generator is available
if ! command -v openapi-generator-cli &> /dev/null; then
    echo "Installing openapi-generator-cli..."
    npm install -g @openapitools/openapi-generator-cli
fi

# Initialize OpenAPI generator (download the JAR)
npx @openapitools/openapi-generator-cli version-manager set 7.0.1 >/dev/null 2>&1

# Check if OpenAPI spec exists
if [ ! -f "$ARTIFACTS_DIR/openapi.yaml" ]; then
    echo "❌ OpenAPI 3.0 spec not found at $ARTIFACTS_DIR/openapi.yaml"
    echo "Run ./scripts/generate-swagger.sh first"
    exit 1
fi

# Create output directories
mkdir -p "$CLIENTS_DIR/go"
mkdir -p "$CLIENTS_DIR/typescript"
mkdir -p "$CLIENTS_DIR/python"

echo "Generating Go client..."
# Generate Go client from OpenAPI 3.0 spec
npx @openapitools/openapi-generator-cli generate \
    -i "$ARTIFACTS_DIR/openapi.yaml" \
    -g go \
    -o "$CLIENTS_DIR/go" \
    --additional-properties=packageName=zeropoint,packageVersion=1.0.0,isGoSubmodule=true

echo "Generating TypeScript client..."
# Generate TypeScript client from OpenAPI 3.0 spec
npx @openapitools/openapi-generator-cli generate \
    -i "$ARTIFACTS_DIR/openapi.yaml" \
    -g typescript-fetch \
    -o "$CLIENTS_DIR/typescript" \
    --additional-properties=npmName=zeropoint-client,supportsES6=true,npmVersion=1.0.0

echo "Generating Python client..."
# Generate Python client from OpenAPI 3.0 spec
npx @openapitools/openapi-generator-cli generate \
    -i "$ARTIFACTS_DIR/openapi.yaml" \
    -g python \
    -o "$CLIENTS_DIR/python" \
    --additional-properties=packageName=zeropoint_client,projectName=zeropoint-client,packageVersion=1.0.0

echo "✅ Go client: $CLIENTS_DIR/go"
echo "✅ TypeScript client: $CLIENTS_DIR/typescript"
echo "✅ Python client: $CLIENTS_DIR/python"
