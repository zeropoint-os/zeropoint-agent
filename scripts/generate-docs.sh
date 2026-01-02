#!/bin/bash
# Generate documentation from OpenAPI spec

set -e

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ARTIFACTS_DIR=${ARTIFACTS_DIR:-"$SCRIPT_DIR/../artifacts"}
DOCS_DIR="$ARTIFACTS_DIR/docs"

echo "Generating documentation from OpenAPI spec..."

# Check if OpenAPI spec exists
if [ ! -f "$ARTIFACTS_DIR/openapi.yaml" ]; then
    echo "❌ OpenAPI 3.0 spec not found at $ARTIFACTS_DIR/openapi.yaml"
    echo "Run ./scripts/generate-swagger.sh first"
    exit 1
fi

# Create output directory
mkdir -p "$DOCS_DIR"

echo "Generating markdown documentation..."
# Generate markdown from OpenAPI 3.0 spec using Python CLI module
python3 /usr/local/lib/python3.11/dist-packages/openapi_markdown/bin/cli.py "$ARTIFACTS_DIR/openapi.yaml" "$DOCS_DIR/API.md"

echo "✅ API documentation generated: $DOCS_DIR/API.md"

# Optionally generate HTML version
if command -v pandoc &> /dev/null; then
    echo "Generating HTML documentation..."
    pandoc "$DOCS_DIR/API.md" -o "$DOCS_DIR/API.html" --standalone --toc
    echo "✅ HTML documentation: $DOCS_DIR/API.html"
fi