package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"zeropoint-agent/internal/api"
	"zeropoint-agent/internal/docker"
	"zeropoint-agent/internal/storage"
)

func main() {
	// Basic setup
	dbPath := "data/zeropoint.db"

	var store storage.Storage
	boltStore, err := storage.NewBoltStore(dbPath)
	if err != nil {
		log.Fatalf("failed to create store: %v", err)
	}
	store = boltStore
	if err := store.Open(); err != nil {
		log.Fatalf("failed to open store: %v", err)
	}
	defer store.Close()

	dockerClient, err := docker.NewClient()
	if err != nil {
		log.Fatalf("failed to create docker client: %v", err)
	}
	defer dockerClient.Close()

	router := api.NewRouter(store, dockerClient)

	srv := &http.Server{
		Addr:    ":8080",
		Handler: router,
	}

	// Start server
	go func() {
		log.Printf("starting server on %s", srv.Addr)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("http server error: %v", err)
		}
	}()

	// Wait for shutdown signal
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	<-stop

	log.Println("shutting down server...")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := srv.Shutdown(ctx); err != nil {
		log.Fatalf("server shutdown failed: %v", err)
	}
	log.Println("server stopped")
}
