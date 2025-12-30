package xds

import (
	"fmt"
	"strings"

	cluster "github.com/envoyproxy/go-control-plane/envoy/config/cluster/v3"
	core "github.com/envoyproxy/go-control-plane/envoy/config/core/v3"
	endpoint "github.com/envoyproxy/go-control-plane/envoy/config/endpoint/v3"
	listener "github.com/envoyproxy/go-control-plane/envoy/config/listener/v3"
	route "github.com/envoyproxy/go-control-plane/envoy/config/route/v3"
	router "github.com/envoyproxy/go-control-plane/envoy/extensions/filters/http/router/v3"
	hcm "github.com/envoyproxy/go-control-plane/envoy/extensions/filters/network/http_connection_manager/v3"
	tcpproxy "github.com/envoyproxy/go-control-plane/envoy/extensions/filters/network/tcp_proxy/v3"
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

// Exposure represents a service exposure (minimal interface to avoid import cycle)
type Exposure struct {
	ID            string
	AppName       string
	Protocol      string
	Hostname      string
	ContainerPort uint32
	HostPort      uint32
}

// BuildSnapshotFromExposures creates a snapshot from a list of exposures
func BuildSnapshotFromExposures(version string, exposures []*Exposure) (*cache.Snapshot, error) {
	var listeners []types.Resource
	var routes []types.Resource
	var clusters []types.Resource

	// Separate HTTP and TCP exposures
	var httpExposures []*Exposure
	var tcpExposures []*Exposure

	for _, exp := range exposures {
		if exp.Protocol == "http" {
			httpExposures = append(httpExposures, exp)
		} else if exp.Protocol == "tcp" {
			tcpExposures = append(tcpExposures, exp)
		}
	}

	// Build HTTP listener and routes if we have HTTP exposures
	if len(httpExposures) > 0 {
		httpListener, err := makeHTTPListener()
		if err != nil {
			return nil, fmt.Errorf("failed to create HTTP listener: %w", err)
		}
		listeners = append(listeners, httpListener)

		// Build route config with all HTTP exposures
		routeConfig := makeRouteConfigFromExposures(httpExposures)
		routes = append(routes, routeConfig)

		// Build clusters for HTTP exposures
		for _, exp := range httpExposures {
			clusterName := fmt.Sprintf("cluster_%s", exp.ID)
			cluster := makeCluster(clusterName, exp.AppName, exp.ContainerPort)
			clusters = append(clusters, cluster)
		}
	} else {
		// No HTTP exposures, use empty route config
		httpListener, err := makeHTTPListener()
		if err != nil {
			return nil, fmt.Errorf("failed to create HTTP listener: %w", err)
		}
		listeners = append(listeners, httpListener)
		routes = append(routes, makeEmptyRouteConfig())
	}

	// Build TCP listeners for TCP exposures
	for _, exp := range tcpExposures {
		tcpListener, err := makeTCPListener(exp.ID, exp.HostPort, exp.AppName, exp.ContainerPort)
		if err != nil {
			return nil, fmt.Errorf("failed to create TCP listener for %s: %w", exp.ID, err)
		}
		listeners = append(listeners, tcpListener)

		clusterName := fmt.Sprintf("cluster_%s", exp.ID)
		cluster := makeCluster(clusterName, exp.AppName, exp.ContainerPort)
		clusters = append(clusters, cluster)
	}

	// Build snapshot
	snapshot, err := cache.NewSnapshot(
		version,
		map[resource.Type][]types.Resource{
			resource.ClusterType:  clusters,
			resource.RouteType:    routes,
			resource.ListenerType: listeners,
		},
	)
	if err != nil {
		return nil, fmt.Errorf("failed to create snapshot: %w", err)
	}

	return snapshot, nil
}

// makeRouteConfigFromExposures creates a route configuration from HTTP exposures
func makeRouteConfigFromExposures(exposures []*Exposure) *route.RouteConfiguration {
	virtualHosts := make([]*route.VirtualHost, 0, len(exposures))

	for _, exp := range exposures {
		clusterName := fmt.Sprintf("cluster_%s", exp.ID)

		// Match both hostname and hostname.local for mDNS compatibility
		domains := []string{exp.Hostname}
		if !strings.HasSuffix(exp.Hostname, ".local") {
			domains = append(domains, exp.Hostname+".local")
		}

		virtualHost := &route.VirtualHost{
			Name:    exp.Hostname,
			Domains: domains,
			Routes: []*route.Route{
				{
					Match: &route.RouteMatch{
						PathSpecifier: &route.RouteMatch_Prefix{
							Prefix: "/",
						},
					},
					Action: &route.Route_Route{
						Route: &route.RouteAction{
							ClusterSpecifier: &route.RouteAction_Cluster{
								Cluster: clusterName,
							},
						},
					},
				},
			},
		}
		virtualHosts = append(virtualHosts, virtualHost)
	}

	return &route.RouteConfiguration{
		Name:         "http_routes",
		VirtualHosts: virtualHosts,
	}
}

// makeTCPListener creates a TCP listener for a specific port
func makeTCPListener(id string, hostPort uint32, targetHost string, targetPort uint32) (*listener.Listener, error) {
	clusterName := fmt.Sprintf("cluster_%s", id)

	tcpProxy := &tcpproxy.TcpProxy{
		StatPrefix: fmt.Sprintf("tcp_%s", id),
		ClusterSpecifier: &tcpproxy.TcpProxy_Cluster{
			Cluster: clusterName,
		},
	}

	pbst, err := anypb.New(tcpProxy)
	if err != nil {
		return nil, err
	}

	return &listener.Listener{
		Name: fmt.Sprintf("tcp_listener_%s", id),
		Address: &core.Address{
			Address: &core.Address_SocketAddress{
				SocketAddress: &core.SocketAddress{
					Protocol: core.SocketAddress_TCP,
					Address:  "0.0.0.0",
					PortSpecifier: &core.SocketAddress_PortValue{
						PortValue: hostPort,
					},
				},
			},
		},
		FilterChains: []*listener.FilterChain{
			{
				Filters: []*listener.Filter{
					{
						Name: wellknown.TCPProxy,
						ConfigType: &listener.Filter_TypedConfig{
							TypedConfig: pbst,
						},
					},
				},
			},
		},
	}, nil
}
