package main

import (
	"context"
	"log"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"zeropoint-agent/internal/api"
	"zeropoint-agent/internal/boot"
	"zeropoint-agent/internal/envoy"
	"zeropoint-agent/internal/mdns"
	"zeropoint-agent/internal/xds"

	"github.com/moby/moby/client"
	"github.com/spf13/cobra"
)

// @title ZeroPoint Agent API
// @version 0.0.0-dev
// @description Zeropoint OS agent REST API specification

// @contact.name API Support
// @contact.url https://github.com/zeropoint-os/zeropoint-agent

// @license.name Apache 2.0
// @license.url https://www.apache.org/licenses/LICENSE-2.0

// @BasePath /

var (
	// Version is set at build time via ldflags
	version = "0.0.0-dev"
)

func main() {
	rootCmd := &cobra.Command{
		Use:     "zeropoint-agent",
		Short:   "ZeroPoint Agent - Application management service",
		Version: version,
		Run:     run,
		// Disable automatic version flag to avoid conflicts
		SilenceUsage: true,
	}

	// Customize version output to only print version string
	rootCmd.SetVersionTemplate("{{.Version}}\n")

	if err := rootCmd.Execute(); err != nil {
		os.Exit(1)
	}
}

func run(cmd *cobra.Command, args []string) {
	// Setup structured logging
	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	}))
	slog.SetDefault(logger)

	logger.Info("zeropoint-agent starting")

	dockerClient, err := client.NewClientWithOpts(client.FromEnv, client.WithAPIVersionNegotiation())
	if err != nil {
		log.Fatalf("failed to create docker client: %v", err)
	}
	defer dockerClient.Close()

	// Start Envoy proxy
	envoyMgr := envoy.NewManager(dockerClient, logger)
	if err := envoyMgr.EnsureRunning(context.Background()); err != nil {
		log.Fatalf("failed to start envoy: %v", err)
	}

	// Start xDS control plane
	logger.Info("initializing xDS server")
	xdsServer := xds.NewServer(logger)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	logger.Info("starting xDS server on port 18000")
	if err := xdsServer.Start(ctx, 18000); err != nil {
		log.Fatalf("failed to start xDS server: %v", err)
	}
	logger.Info("xDS server started successfully")

	// Give xDS server time to start
	time.Sleep(500 * time.Millisecond)

	// Create initial empty snapshot
	snapshot, err := xds.BuildSnapshot(xdsServer.NextVersion())
	if err != nil {
		log.Fatalf("failed to build initial snapshot: %v", err)
	}

	logger.Info("setting initial snapshot", "version", snapshot.GetVersion("listeners"))
	if err := xdsServer.UpdateSnapshot(context.Background(), snapshot); err != nil {
		log.Fatalf("failed to set initial snapshot: %v", err)
	}

	// Get port from environment variable, default to 2370
	portStr := os.Getenv("ZEROPOINT_AGENT_PORT")
	if portStr == "" {
		portStr = "2370"
	}

	portNum, err := strconv.Atoi(portStr)
	if err != nil {
		log.Fatalf("invalid port number: %v", err)
	}

	// Register mDNS service (before router so it's available for exposures)
	mdnsService := mdns.NewService(logger)
	if err := mdnsService.Register(context.Background(), portNum); err != nil {
		logger.Warn("failed to register mDNS service", "error", err)
	}
	defer mdnsService.Shutdown()

	// Initialize boot monitor
	bootMonitor := boot.NewBootMonitor(logger)

	// Start boot monitoring in background
	go func() {
		if err := bootMonitor.StreamJournal(); err != nil {
			logger.Error("boot monitor error", "error", err)
		}
	}()

	router, err := api.NewRouter(dockerClient, xdsServer, mdnsService, bootMonitor, logger)
	if err != nil {
		log.Fatalf("failed to create router: %v", err)
	}

	srv := &http.Server{
		Addr:    ":" + portStr,
		Handler: router,
	}

	// Start server
	go func() {
		logger.Info("starting server", "addr", srv.Addr)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("http server error: %v", err)
		}
	}()

	// Wait for shutdown signal
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	<-stop

	logger.Info("shutting down server")
	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer shutdownCancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		log.Fatalf("server shutdown failed: %v", err)
	}
	logger.Info("server stopped")
}
