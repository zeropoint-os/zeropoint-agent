package xds

import (
	"fmt"

	cluster "github.com/envoyproxy/go-control-plane/envoy/config/cluster/v3"
	core "github.com/envoyproxy/go-control-plane/envoy/config/core/v3"
	endpoint "github.com/envoyproxy/go-control-plane/envoy/config/endpoint/v3"
	listener "github.com/envoyproxy/go-control-plane/envoy/config/listener/v3"
	route "github.com/envoyproxy/go-control-plane/envoy/config/route/v3"
	router "github.com/envoyproxy/go-control-plane/envoy/extensions/filters/http/router/v3"
	hcm "github.com/envoyproxy/go-control-plane/envoy/extensions/filters/network/http_connection_manager/v3"
	"github.com/envoyproxy/go-control-plane/pkg/cache/types"
	"github.com/envoyproxy/go-control-plane/pkg/cache/v3"
	"github.com/envoyproxy/go-control-plane/pkg/resource/v3"
	"github.com/envoyproxy/go-control-plane/pkg/wellknown"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/anypb"
	"google.golang.org/protobuf/types/known/durationpb"
)

// BuildSnapshot creates a snapshot with listeners, routes, and clusters
func BuildSnapshot(version string) (*cache.Snapshot, error) {
	// Create HTTP listener on port 80
	httpListener, err := makeHTTPListener()
	if err != nil {
		return nil, fmt.Errorf("failed to create HTTP listener: %w", err)
	}

	// Create empty route configuration (returns 404 for everything)
	routeConfig := makeEmptyRouteConfig()

	// Build snapshot with all resources
	snapshot, err := cache.NewSnapshot(
		version,
		map[resource.Type][]types.Resource{
			resource.ClusterType:  {}, // Empty clusters for now
			resource.RouteType:    {routeConfig},
			resource.ListenerType: {httpListener},
		},
	)
	if err != nil {
		return nil, fmt.Errorf("failed to create snapshot: %w", err)
	}

	return snapshot, nil
}

// makeHTTPListener creates a listener on port 80 with HTTP connection manager
func makeHTTPListener() (*listener.Listener, error) {
	// Create HTTP connection manager config
	manager := &hcm.HttpConnectionManager{
		CodecType:  hcm.HttpConnectionManager_AUTO,
		StatPrefix: "http",
		RouteSpecifier: &hcm.HttpConnectionManager_Rds{
			Rds: &hcm.Rds{
				ConfigSource: &core.ConfigSource{
					ResourceApiVersion: core.ApiVersion_V3,
					ConfigSourceSpecifier: &core.ConfigSource_Ads{
						Ads: &core.AggregatedConfigSource{},
					},
				},
				RouteConfigName: "http_routes",
			},
		},
		HttpFilters: []*hcm.HttpFilter{
			{
				Name: wellknown.Router,
				ConfigType: &hcm.HttpFilter_TypedConfig{
					TypedConfig: mustMarshalAny(&router.Router{}),
				},
			},
		},
	}

	// Marshal to Any
	pbst, err := anypb.New(manager)
	if err != nil {
		return nil, err
	}

	return &listener.Listener{
		Name: "http_listener",
		Address: &core.Address{
			Address: &core.Address_SocketAddress{
				SocketAddress: &core.SocketAddress{
					Protocol: core.SocketAddress_TCP,
					Address:  "0.0.0.0",
					PortSpecifier: &core.SocketAddress_PortValue{
						PortValue: 80,
					},
				},
			},
		},
		FilterChains: []*listener.FilterChain{
			{
				Filters: []*listener.Filter{
					{
						Name: wellknown.HTTPConnectionManager,
						ConfigType: &listener.Filter_TypedConfig{
							TypedConfig: pbst,
						},
					},
				},
			},
		},
	}, nil
}

// makeEmptyRouteConfig creates a route configuration that returns 404 for all requests
func makeEmptyRouteConfig() *route.RouteConfiguration {
	return &route.RouteConfiguration{
		Name: "http_routes",
		VirtualHosts: []*route.VirtualHost{
			{
				Name:    "default_backend",
				Domains: []string{"*"},
				Routes: []*route.Route{
					{
						Match: &route.RouteMatch{
							PathSpecifier: &route.RouteMatch_Prefix{
								Prefix: "/",
							},
						},
						Action: &route.Route_DirectResponse{
							DirectResponse: &route.DirectResponseAction{
								Status: 404,
								Body: &core.DataSource{
									Specifier: &core.DataSource_InlineString{
										InlineString: "No apps exposed\n",
									},
								},
							},
						},
					},
				},
			},
		},
	}
}

// makeCluster creates a cluster for an app service
func makeCluster(name string, host string, port uint32) *cluster.Cluster {
	return &cluster.Cluster{
		Name:                 name,
		ConnectTimeout:       durationpb.New(5 * 1000000000), // 5 seconds in nanoseconds
		ClusterDiscoveryType: &cluster.Cluster_Type{Type: cluster.Cluster_STRICT_DNS},
		LbPolicy:             cluster.Cluster_ROUND_ROBIN,
		LoadAssignment: &endpoint.ClusterLoadAssignment{
			ClusterName: name,
			Endpoints: []*endpoint.LocalityLbEndpoints{
				{
					LbEndpoints: []*endpoint.LbEndpoint{
						{
							HostIdentifier: &endpoint.LbEndpoint_Endpoint{
								Endpoint: &endpoint.Endpoint{
									Address: &core.Address{
										Address: &core.Address_SocketAddress{
											SocketAddress: &core.SocketAddress{
												Protocol: core.SocketAddress_TCP,
												Address:  host,
												PortSpecifier: &core.SocketAddress_PortValue{
													PortValue: port,
												},
											},
										},
									},
								},
							},
						},
					},
				},
			},
		},
	}
}

// mustMarshalAny marshals a protobuf message to Any, panicking on error
func mustMarshalAny(msg proto.Message) *anypb.Any {
	a, err := anypb.New(msg)
	if err != nil {
		panic(fmt.Sprintf("failed to marshal %T to Any: %v", msg, err))
	}
	if a == nil {
		panic(fmt.Sprintf("anypb.New returned nil for %T", msg))
	}
	if a.TypeUrl == "" {
		panic(fmt.Sprintf("anypb.New returned empty TypeUrl for %T", msg))
	}
	return a
}
