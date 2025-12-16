package apps

// App represents an installed application managed by zeropoint-agent.
type App struct {
	ID             string      `json:"id"`
	RepoURL        string      `json:"repo_url"`
	CheckoutPath   string      `json:"checkout_path"`
	NetworkName    string      `json:"network_name"`
	ContainerPorts []int       `json:"container_ports"`
	HostPorts      map[int]int `json:"host_ports"` // containerPort -> hostPort
	State          string      `json:"state"`
	ContainerIDs   []string    `json:"container_ids"`
}

// App states
const (
	StateInstalled = "installed"
	StateRunning   = "running"
	StateStopped   = "stopped"
	StateRemoved   = "removed"
)
