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

	// Determine which interfaces to use
	var ifaces []net.Interface

	// Check for manual interface override
	if ifaceName := os.Getenv("ZEROPOINT_INTERFACE"); ifaceName != "" {
		iface, err := net.InterfaceByName(ifaceName)
		if err != nil {
			return fmt.Errorf("failed to get interface %s: %w", ifaceName, err)
		}
		ifaces = []net.Interface{*iface}
		s.logger.Info("using manual interface", "interface", ifaceName)
	} else if manualIP := os.Getenv("ZEROPOINT_IP"); manualIP != "" {
		// Find interface with matching IP
		targetIP := net.ParseIP(manualIP)
		if targetIP == nil {
			return fmt.Errorf("invalid IP address: %s", manualIP)
		}

		allIfaces, err := net.Interfaces()
		if err != nil {
			return fmt.Errorf("failed to list interfaces: %w", err)
		}

		found := false
		for _, iface := range allIfaces {
			addrs, err := iface.Addrs()
			if err != nil {
				continue
			}
			for _, addr := range addrs {
				if ipnet, ok := addr.(*net.IPNet); ok && ipnet.IP.Equal(targetIP) {
					ifaces = []net.Interface{iface}
					found = true
					break
				}
			}
			if found {
				break
			}
		}

		if !found {
			return fmt.Errorf("no interface found with IP %s", manualIP)
		}
		s.logger.Info("using interface for IP", "ip", manualIP, "interface", ifaces[0].Name)
	} else {
		// Auto-detect: exclude Docker/internal networks
		allIfaces, err := net.Interfaces()
		if err != nil {
			return fmt.Errorf("failed to list interfaces: %w", err)
		}

		for _, iface := range allIfaces {
			// Skip loopback and down interfaces
			if iface.Flags&net.FlagLoopback != 0 || iface.Flags&net.FlagUp == 0 {
				continue
			}

			addrs, err := iface.Addrs()
			if err != nil {
				continue
			}

			// Check if interface has non-Docker IPv4 address
			hasValidIP := false
			for _, addr := range addrs {
				if ipnet, ok := addr.(*net.IPNet); ok {
					if ipv4 := ipnet.IP.To4(); ipv4 != nil && !isDockerNetwork(ipv4) {
						hasValidIP = true
						break
					}
				}
			}

			if hasValidIP {
				ifaces = append(ifaces, iface)
			}
		}
	}

	if len(ifaces) == 0 {
		return fmt.Errorf("no suitable network interfaces found")
	}

	// Register the service on selected interfaces
	server, err := zeroconf.Register(
		hostname,                // instance name
		"_zeropoint-agent._tcp", // service type
		"local.",                // domain
		port,                    // port
		[]string{ // TXT records
			"version=0.0.0-dev",
			"api=rest",
		},
		ifaces, // use specific interfaces (not all)
	)
	if err != nil {
		return fmt.Errorf("failed to register mDNS service: %w", err)
	}

	s.server = server

	// Log selected interfaces
	ifaceNames := make([]string, len(ifaces))
	for i, iface := range ifaces {
		ifaceNames[i] = iface.Name
	}

	s.logger.Info("registered mDNS service",
		"hostname", hostname,
		"service", "_zeropoint-agent._tcp",
		"port", port,
		"interfaces", ifaceNames,
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
