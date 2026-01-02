package mdns

import (
	"context"
	"fmt"
	"log/slog"
	"net"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/grandcat/zeroconf"
)

// Service represents an mDNS service announcer
type Service struct {
	server    *zeroconf.Server
	logger    *slog.Logger
	port      int
	hostname  string
	ifaces    []net.Interface
	ctx       context.Context
	cancel    context.CancelFunc
	exposures map[string]*zeroconf.Server // hostname -> server
	mu        sync.RWMutex
}

// NewService creates a new mDNS service announcer
func NewService(logger *slog.Logger) *Service {
	return &Service{
		logger:    logger,
		exposures: make(map[string]*zeroconf.Server),
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

	// Store registration details for potential re-registration
	s.port = port
	s.hostname = hostname
	s.ifaces = ifaces

	// Create context for supervision
	s.ctx, s.cancel = context.WithCancel(ctx)

	// Start supervision goroutine to monitor and re-register if needed
	go s.supervise()

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
	if s.cancel != nil {
		s.cancel() // Stop supervision goroutine
	}
	if s.server != nil {
		s.server.Shutdown()
		s.logger.Info("mDNS service shutdown")
	}

	// Shutdown all exposure announcements
	s.mu.Lock()
	defer s.mu.Unlock()
	for hostname, server := range s.exposures {
		server.Shutdown()
		s.logger.Info("mDNS exposure unregistered", "hostname", hostname)
	}
	s.exposures = make(map[string]*zeroconf.Server)
}

// RegisterExposure registers an exposure hostname via mDNS
func (s *Service) RegisterExposure(hostname string, port int) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	// Check if already registered
	if _, exists := s.exposures[hostname]; exists {
		s.logger.Info("mDNS exposure already registered", "hostname", hostname)
		return nil
	}

	// Use stored interfaces from agent registration
	ifaces := s.ifaces
	if len(ifaces) == 0 {
		s.logger.Warn("no interfaces available for exposure registration, skipping", "hostname", hostname)
		return nil
	}

	// Get IP addresses from interfaces
	var ips []string
	for _, iface := range ifaces {
		addrs, err := iface.Addrs()
		if err != nil {
			continue
		}
		for _, addr := range addrs {
			if ipnet, ok := addr.(*net.IPNet); ok {
				if ipv4 := ipnet.IP.To4(); ipv4 != nil {
					ips = append(ips, ipv4.String())
				}
			}
		}
	}

	if len(ips) == 0 {
		return fmt.Errorf("no IP addresses found for mDNS exposure registration")
	}

	// Register as HTTP service with custom hostname using RegisterProxy
	// Strip .local suffix if present since zeroconf adds it automatically
	// e.g., "openwebui-zeropoint-bright-river.local" -> "openwebui-zeropoint-bright-river"
	instanceName := strings.TrimSuffix(hostname, ".local")

	server, err := zeroconf.RegisterProxy(
		instanceName, // instance name (without .local)
		"_http._tcp", // service type (HTTP service)
		"local.",     // domain
		port,         // port (80 for HTTP through Envoy)
		instanceName, // hostname (without .local - domain parameter adds it)
		ips,          // IP addresses
		[]string{ // TXT records
			"path=/",
			"exposure=true",
		},
		ifaces, // use same interfaces as agent
	)
	if err != nil {
		return fmt.Errorf("failed to register mDNS exposure: %w", err)
	}

	s.exposures[hostname] = server
	s.logger.Info("mDNS exposure registered", "hostname", hostname, "port", port)
	return nil
}

// UnregisterExposure removes an exposure hostname from mDNS
func (s *Service) UnregisterExposure(hostname string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	server, exists := s.exposures[hostname]
	if !exists {
		s.logger.Warn("mDNS exposure not found for unregistration", "hostname", hostname)
		return nil
	}

	server.Shutdown()
	delete(s.exposures, hostname)
	s.logger.Info("mDNS exposure unregistered", "hostname", hostname)
	return nil
}

// ReregisterAllExposures re-registers all exposures from a list (called on startup)
func (s *Service) ReregisterAllExposures(exposures []ExposureInfo) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	// Clear existing exposure registrations
	for hostname, server := range s.exposures {
		if server != nil {
			server.Shutdown()
		}
		s.logger.Debug("cleared old mDNS exposure", "hostname", hostname)
	}
	s.exposures = make(map[string]*zeroconf.Server)

	// Register all exposures
	for _, exp := range exposures {
		// Only register HTTP exposures with hostnames
		if exp.Protocol != "http" || exp.Hostname == "" {
			continue
		}

		server, err := zeroconf.Register(
			exp.Hostname,
			"_http._tcp",
			"local.",
			80, // HTTP port through Envoy
			[]string{
				"path=/",
				"exposure=true",
			},
			s.ifaces,
		)
		if err != nil {
			s.logger.Warn("failed to register mDNS for exposure on startup", "hostname", exp.Hostname, "error", err)
			continue
		}

		s.exposures[exp.Hostname] = server
		s.logger.Info("re-registered mDNS exposure on startup", "hostname", exp.Hostname)
	}

	return nil
}

// ExposureInfo contains the minimal info needed for mDNS registration
type ExposureInfo struct {
	Protocol string
	Hostname string
}

// supervise monitors the mDNS service and refreshes TTL
func (s *Service) supervise() {
	ticker := time.NewTicker(60 * time.Second) // Refresh TTL every 60 seconds
	defer ticker.Stop()

	for {
		select {
		case <-s.ctx.Done():
			return
		case <-ticker.C:
			s.logger.Debug("refreshing mDNS TTL for all services")

			// Refresh agent registration without downtime
			s.refreshAgent()

			// Refresh all exposure registrations without downtime
			s.refreshAllExposures()
		}
	}
}

// refreshAgent refreshes the agent mDNS registration without downtime
func (s *Service) refreshAgent() {
	oldServer := s.server

	// Create new registration
	newServer, err := zeroconf.Register(
		s.hostname,
		"_zeropoint-agent._tcp",
		"local.",
		s.port,
		[]string{
			"version=0.0.0-dev",
			"api=rest",
		},
		s.ifaces,
	)
	if err != nil {
		s.logger.Error("failed to refresh agent mDNS service", "error", err)
		return
	}

	// Update to new server
	s.server = newServer

	// Shutdown old server after new one is live
	if oldServer != nil {
		oldServer.Shutdown()
	}
}

// refreshAllExposures refreshes all exposure mDNS registrations without downtime
func (s *Service) refreshAllExposures() {
	s.mu.Lock()
	defer s.mu.Unlock()

	for hostname, oldServer := range s.exposures {
		// Get IP addresses from interfaces
		var ips []string
		for _, iface := range s.ifaces {
			addrs, err := iface.Addrs()
			if err != nil {
				continue
			}
			for _, addr := range addrs {
				if ipnet, ok := addr.(*net.IPNet); ok {
					if ipv4 := ipnet.IP.To4(); ipv4 != nil {
						ips = append(ips, ipv4.String())
					}
				}
			}
		}

		if len(ips) == 0 {
			s.logger.Warn("no IPs found for exposure refresh", "hostname", hostname)
			continue
		}

		// Convert dots to dashes for instance name
		instanceName := strings.TrimSuffix(hostname, ".local")

		// Create new registration
		newServer, err := zeroconf.RegisterProxy(
			instanceName,
			"_http._tcp",
			"local.",
			80,
			instanceName,
			ips,
			[]string{
				"path=/",
				"exposure=true",
			},
			s.ifaces,
		)
		if err != nil {
			s.logger.Warn("failed to refresh mDNS for exposure", "hostname", hostname, "error", err)
			continue
		}

		// Update to new server
		s.exposures[hostname] = newServer

		// Shutdown old server after new one is live
		if oldServer != nil {
			oldServer.Shutdown()
		}
	}
}

// reregister attempts to re-register the mDNS service
func (s *Service) reregister() error {
	if s.server != nil {
		s.server.Shutdown()
		s.server = nil
	}

	server, err := zeroconf.Register(
		s.hostname,
		"_zeropoint-agent._tcp",
		"local.",
		s.port,
		[]string{
			"version=0.0.0-dev",
			"api=rest",
		},
		s.ifaces,
	)
	if err != nil {
		return err
	}

	s.server = server
	s.logger.Info("re-registered mDNS service")
	return nil
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
