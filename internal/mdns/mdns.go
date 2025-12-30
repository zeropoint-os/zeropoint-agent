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

	// Get local IP addresses
	addrs, err := net.InterfaceAddrs()
	if err != nil {
		return fmt.Errorf("failed to get network interfaces: %w", err)
	}

	var ips []string
	for _, addr := range addrs {
		if ipnet, ok := addr.(*net.IPNet); ok && !ipnet.IP.IsLoopback() {
			if ipnet.IP.To4() != nil {
				ips = append(ips, ipnet.IP.String())
			}
		}
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
