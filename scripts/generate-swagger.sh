#!/bin/bash
# Generate Swagger documentation from Go code

set -e

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ARTIFACTS_DIR=${ARTIFACTS_DIR:-"$SCRIPT_DIR/../artifacts"}
VERSION=${VERSION:-"0.0.0-dev"}

echo "Generating Swagger documentation..."

# Ensure swag is installed
if ! command -v swag &> /dev/null; then
    echo "Installing swag..."
    go install github.com/swaggo/swag/cmd/swag@latest
fi

# Generate swagger docs to ./docs (for Go import)
swag init -g ./cmd/zeropoint-agent/main.go -o ./docs

# Update version in generated specs
sed -i "s/version: 0.0.0-dev/version: $VERSION/" ./docs/swagger.yaml
sed -i "s/\"version\": \"0.0.0-dev\"/\"version\": \"$VERSION\"/" ./docs/swagger.json

# Copy only the OpenAPI spec to artifacts
mkdir -p "$ARTIFACTS_DIR"
cp ./docs/swagger.* "$ARTIFACTS_DIR/"

echo "✅ OpenAPI spec: $ARTIFACTS_DIR"
