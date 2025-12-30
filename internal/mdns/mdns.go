package mdns

import (
	"context"
	"fmt"
	"log/slog"
	"net"
	"os"
	"time"

	"github.com/grandcat/zeroconf"
)

// Service represents an mDNS service announcer
type Service struct {
	server *zeroconf.Server
	logger *slog.Logger
}

// NewService creates a new mDNS service announcer
func NewService(logger *slog.Logger) *Service {
	return &Service{
		logger: logger,
	}
}

// Register announces the zeropoint-agent service via mDNS
func (s *Service) Register(ctx context.Context, port int) error {
	hostname, err := os.Hostname()
	if err != nil {
		return fmt.Errorf("failed to get hostname: %w", err)
	}

	// Check for manual IP override
	var ips []string
	if manualIP := os.Getenv("ZEROPOINT_IP"); manualIP != "" {
		ips = []string{manualIP}
		s.logger.Info("using manual IP override", "ip", manualIP)
	} else if ifaceName := os.Getenv("ZEROPOINT_INTERFACE"); ifaceName != "" {
		// Get IP from specific interface
		iface, err := net.InterfaceByName(ifaceName)
		if err != nil {
			return fmt.Errorf("failed to get interface %s: %w", ifaceName, err)
		}
		addrs, err := iface.Addrs()
		if err != nil {
			return fmt.Errorf("failed to get addresses for interface %s: %w", ifaceName, err)
		}
		for _, addr := range addrs {
			if ipnet, ok := addr.(*net.IPNet); ok && ipnet.IP.To4() != nil {
				ips = append(ips, ipnet.IP.String())
			}
		}
		s.logger.Info("using interface", "interface", ifaceName, "ips", ips)
	} else {
		// Auto-detect: get all IPs excluding Docker/internal networks
		addrs, err := net.InterfaceAddrs()
		if err != nil {
			return fmt.Errorf("failed to get network interfaces: %w", err)
		}

		for _, addr := range addrs {
			if ipnet, ok := addr.(*net.IPNet); ok && !ipnet.IP.IsLoopback() {
				if ipv4 := ipnet.IP.To4(); ipv4 != nil && !isDockerNetwork(ipv4) {
					ips = append(ips, ipv4.String())
				}
			}
		}
	}

	if len(ips) == 0 {
		return fmt.Errorf("no suitable network interfaces found")
	}

	// Register the service
	server, err := zeroconf.Register(
		hostname,                // instance name
		"_zeropoint-agent._tcp", // service type
		"local.",                // domain
		port,                    // port
		[]string{ // TXT records
			"version=0.0.0-dev",
			"api=rest",
		},
		nil, // use all network interfaces
	)
	if err != nil {
		return fmt.Errorf("failed to register mDNS service: %w", err)
	}

	s.server = server
	s.logger.Info("registered mDNS service",
		"hostname", hostname,
		"service", "_zeropoint-agent._tcp",
		"port", port,
		"ips", ips,
	)

	return nil
}

// isDockerNetwork checks if an IP is in common Docker network ranges
func isDockerNetwork(ip net.IP) bool {
	// Docker default bridge: 172.17.0.0/16
	// Docker custom bridges: 172.16.0.0/12 (172.16.0.0 - 172.31.255.255)
	// Docker compose: 172.x.x.x ranges
	// Common internal ranges: 10.0.0.0/8
	dockerRanges := []string{
		"172.16.0.0/12", // Docker user-defined networks
		"10.0.0.0/8",    // Common internal networks
	}

	for _, cidr := range dockerRanges {
		_, network, _ := net.ParseCIDR(cidr)
		if network.Contains(ip) {
			return true
		}
	}
	return false
}

// Shutdown stops the mDNS service announcement
func (s *Service) Shutdown() {
	if s.server != nil {
		s.server.Shutdown()
		s.logger.Info("mDNS service shutdown")
	}
}

// Discover finds zeropoint-agent instances on the local network
func Discover(ctx context.Context, timeout time.Duration) ([]*zeroconf.ServiceEntry, error) {
	resolver, err := zeroconf.NewResolver(nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create resolver: %w", err)
	}

	entries := make(chan *zeroconf.ServiceEntry)
	var results []*zeroconf.ServiceEntry

	go func() {
		for entry := range entries {
			results = append(results, entry)
		}
	}()

	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	err = resolver.Browse(ctx, "_zeropoint-agent._tcp", "local.", entries)
	if err != nil {
		return nil, fmt.Errorf("failed to browse services: %w", err)
	}

	<-ctx.Done()
	return results, nil
}

// FormatServiceURL returns the HTTP URL for a discovered service
func FormatServiceURL(entry *zeroconf.ServiceEntry) string {
	if len(entry.AddrIPv4) > 0 {
		return fmt.Sprintf("http://%s:%d", entry.AddrIPv4[0].String(), entry.Port)
	}
	if len(entry.AddrIPv6) > 0 {
		return fmt.Sprintf("http://[%s]:%d", entry.AddrIPv6[0].String(), entry.Port)
	}
	return fmt.Sprintf("http://%s:%d", entry.HostName, entry.Port)
}
