package envoy

import (
	"fmt"
	"os"
	"path/filepath"

	"zeropoint-agent/internal/apps"
)

const bootstrapTemplate = `node:
  id: zeropoint-node
  cluster: zeropoint-cluster

dynamic_resources:
  ads_config:
    api_type: GRPC
    grpc_services:
      - envoy_grpc:
          cluster_name: xds_cluster
  cds_config: {ads: {}}
  lds_config: {ads: {}}

static_resources:
  clusters:
    - name: xds_cluster
      type: STATIC
      connect_timeout: 1s
      http2_protocol_options: {}
      load_assignment:
        cluster_name: xds_cluster
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: %s
                      port_value: %d

admin:
  address:
    socket_address:
      address: 0.0.0.0
      port_value: 9901
`

// GetBootstrapPath returns the path to the Envoy bootstrap configuration file.
// Creates the file if it doesn't exist.
func GetBootstrapPath(xdsHost string, xdsPort int) (string, error) {
	envoyDir := filepath.Join(apps.GetStorageRoot(), "envoy")

	// Convert to absolute path for Docker bind mount
	absEnvoyDir, err := filepath.Abs(envoyDir)
	if err != nil {
		return "", fmt.Errorf("failed to get absolute path: %w", err)
	}

	if err := os.MkdirAll(absEnvoyDir, 0755); err != nil {
		return "", fmt.Errorf("failed to create envoy directory: %w", err)
	}

	bootstrapPath := filepath.Join(absEnvoyDir, "bootstrap.yaml")

	// Generate bootstrap config with xDS host and port
	config := fmt.Sprintf(bootstrapTemplate, xdsHost, xdsPort)

	// Write bootstrap config
	if err := os.WriteFile(bootstrapPath, []byte(config), 0644); err != nil {
		return "", fmt.Errorf("failed to write bootstrap config: %w", err)
	}

	return bootstrapPath, nil
}
