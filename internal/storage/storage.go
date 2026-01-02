package storage

import "zeropoint-agent/internal/modules"

// Storage is an abstract persistent store for application metadata.
// Implementations may be local (BoltDB) or remote (etcd, Postgres, Redis).
type Storage interface {
	Open() error
	Close() error

	// App operations
	GetApps() ([]apps.App, error)
	GetApp(id string) (apps.App, error)
	SaveApp(a apps.App) error
	DeleteApp(id string) error
}
