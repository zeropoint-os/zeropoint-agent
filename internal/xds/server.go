package xds

import (
"context"
"fmt"
"log/slog"
"net"
"sync/atomic"

clusterservice "github.com/envoyproxy/go-control-plane/envoy/service/cluster/v3"
discoverygrpc "github.com/envoyproxy/go-control-plane/envoy/service/discovery/v3"
endpointservice "github.com/envoyproxy/go-control-plane/envoy/service/endpoint/v3"
listenerservice "github.com/envoyproxy/go-control-plane/envoy/service/listener/v3"
routeservice "github.com/envoyproxy/go-control-plane/envoy/service/route/v3"
"github.com/envoyproxy/go-control-plane/pkg/cache/v3"
"github.com/envoyproxy/go-control-plane/pkg/resource/v3"
xdsserver "github.com/envoyproxy/go-control-plane/pkg/server/v3"
"google.golang.org/grpc"
)

const (
// NodeID that Envoy uses in bootstrap config
nodeID = "zeropoint-node"
)

// Server manages the xDS control plane for Envoy
type Server struct {
cache   cache.SnapshotCache
server  xdsserver.Server
logger  *slog.Logger
version atomic.Uint64
}

// NewServer creates a new xDS control plane server
func NewServer(logger *slog.Logger) *Server {
// Create snapshot cache (pass nil for logger to avoid interface issues)
snapshotCache := cache.NewSnapshotCache(false, cache.IDHash{}, nil)

// Create xDS server
srv := xdsserver.NewServer(context.Background(), snapshotCache, nil)

return &Server{
cache:  snapshotCache,
server: srv,
logger: logger,
}
}

// Start starts the xDS gRPC server
func (s *Server) Start(ctx context.Context, port int) error {
lis, err := net.Listen("tcp", fmt.Sprintf(":%d", port))
if err != nil {
return fmt.Errorf("failed to listen on port %d: %w", port, err)
}

grpcServer := grpc.NewServer()

// Register xDS services
discoverygrpc.RegisterAggregatedDiscoveryServiceServer(grpcServer, s.server)
endpointservice.RegisterEndpointDiscoveryServiceServer(grpcServer, s.server)
clusterservice.RegisterClusterDiscoveryServiceServer(grpcServer, s.server)
routeservice.RegisterRouteDiscoveryServiceServer(grpcServer, s.server)
listenerservice.RegisterListenerDiscoveryServiceServer(grpcServer, s.server)

s.logger.Info("xDS server starting", "port", port)

// Start serving (blocks)
go func() {
if err := grpcServer.Serve(lis); err != nil {
s.logger.Error("xDS server error", "error", err)
}
}()

// Graceful shutdown on context cancellation
go func() {
<-ctx.Done()
s.logger.Info("xDS server shutting down")
grpcServer.GracefulStop()
}()

return nil
}

// UpdateSnapshot updates the Envoy configuration snapshot
func (s *Server) UpdateSnapshot(ctx context.Context, snapshot *cache.Snapshot) error {
if snapshot == nil {
return fmt.Errorf("snapshot cannot be nil")
}

if err := s.cache.SetSnapshot(ctx, nodeID, snapshot); err != nil {
return fmt.Errorf("failed to set snapshot: %w", err)
}

s.logger.Info("snapshot updated", "version", snapshot.GetVersion(resource.ListenerType))
return nil
}

// NextVersion returns the next monotonic version number
func (s *Server) NextVersion() string {
v := s.version.Add(1)
return fmt.Sprintf("v%d", v)
}
