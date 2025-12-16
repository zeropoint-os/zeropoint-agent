package docker

import (
	"context"

	dockerclient "github.com/moby/moby/client"
)

// Client wraps the underlying Docker SDK client.
type Client struct {
	cli *dockerclient.Client
}

// NewClient creates a docker client using environment variables and API negotiation.
func NewClient() (*Client, error) {
	// API version negotiation is enabled by default; call New with env config.
	cli, err := dockerclient.New(dockerclient.FromEnv)
	if err != nil {
		return nil, err
	}
	return &Client{cli: cli}, nil
}

func (c *Client) Close() error {
	if c.cli == nil {
		return nil
	}
	return c.cli.Close()
}

// Raw returns the underlying client for callers that need low-level access.
func (c *Client) Raw() *dockerclient.Client { return c.cli }

// Ping checks connectivity to the Docker daemon.
func (c *Client) Ping(ctx context.Context) error {
	_, err := c.cli.Ping(ctx, dockerclient.PingOptions{})
	return err
}
