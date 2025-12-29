package main

import (
	"context"
	"log"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"zeropoint-agent/internal/api"

	"github.com/moby/moby/client"
	"github.com/spf13/cobra"
)

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

	dockerClient, err := client.NewClientWithOpts(client.FromEnv, client.WithAPIVersionNegotiation())
	if err != nil {
		log.Fatalf("failed to create docker client: %v", err)
	}
	defer dockerClient.Close()

	router := api.NewRouter(dockerClient, logger)

	// Get port from environment variable, default to 2370
	port := os.Getenv("ZEROPOINT_AGENT_PORT")
	if port == "" {
		port = "2370"
	}

	srv := &http.Server{
		Addr:    ":" + port,
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
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		log.Fatalf("server shutdown failed: %v", err)
	}
	logger.Info("server stopped")
}
