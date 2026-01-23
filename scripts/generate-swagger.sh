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

echo "✅ Swagger 2.0 spec generated"

# Convert Swagger 2.0 to OpenAPI 3.0
echo "Converting Swagger 2.0 to OpenAPI 3.0..."
swagger2openapi "$ARTIFACTS_DIR/swagger.json" -o "$ARTIFACTS_DIR/openapi.json"

# Fix the any-type fields to use proper nullable schema without type constraint
# In OAS3, omitting 'type' means any type is allowed
jq '
  .components.schemas."api.InputSchema".properties.current_value = {
    "description": "Current value (can be any JSON type: string, number, boolean, object, or array)",
    "nullable": true
  } |
  .components.schemas."api.InputSchema".properties.default_value = {
    "description": "Default value (can be any JSON type: string, number, boolean, object, or array)",
    "nullable": true
  } |
  .components.schemas."api.OutputSchema".properties.current_value = {
    "description": "Current value (can be any JSON type: string, number, boolean, object, or array)",
    "nullable": true
  }
' "$ARTIFACTS_DIR/openapi.json" > "$ARTIFACTS_DIR/openapi.json.tmp" && mv "$ARTIFACTS_DIR/openapi.json.tmp" "$ARTIFACTS_DIR/openapi.json"

# Convert to YAML for better readability
npx js-yaml "$ARTIFACTS_DIR/openapi.json" > "$ARTIFACTS_DIR/openapi.yaml"

echo "✅ OpenAPI 3.0 spec: $ARTIFACTS_DIR/openapi.json"
echo "✅ OpenAPI 3.0 YAML: $ARTIFACTS_DIR/openapi.yaml"
