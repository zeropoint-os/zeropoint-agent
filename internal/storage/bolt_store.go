package storage

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"

	bolt "go.etcd.io/bbolt"

	"zeropoint-agent/internal/apps"
)

const appsBucket = "apps"

type BoltStore struct {
	path string
	db   *bolt.DB
}

func NewBoltStore(path string) (*BoltStore, error) {
	return &BoltStore{path: path}, nil
}

func (s *BoltStore) Open() error {
	dir := filepath.Dir(s.path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	db, err := bolt.Open(s.path, 0o600, nil)
	if err != nil {
		return err
	}
	s.db = db
	// Ensure bucket
	return s.db.Update(func(tx *bolt.Tx) error {
		_, err := tx.CreateBucketIfNotExists([]byte(appsBucket))
		return err
	})
}

func (s *BoltStore) Close() error {
	if s.db == nil {
		return nil
	}
	return s.db.Close()
}

// GetApps returns all stored apps (may be empty).
func (s *BoltStore) GetApps() ([]apps.App, error) {
	var out []apps.App
	if s.db == nil {
		return out, nil
	}
	err := s.db.View(func(tx *bolt.Tx) error {
		b := tx.Bucket([]byte(appsBucket))
		if b == nil {
			return nil
		}
		return b.ForEach(func(k, v []byte) error {
			var a apps.App
			if err := json.Unmarshal(v, &a); err != nil {
				return err
			}
			out = append(out, a)
			return nil
		})
	})
	return out, err
}

func (s *BoltStore) GetApp(id string) (apps.App, error) {
	var a apps.App
	if s.db == nil {
		return a, errors.New("db not open")
	}
	err := s.db.View(func(tx *bolt.Tx) error {
		b := tx.Bucket([]byte(appsBucket))
		if b == nil {
			return errors.New("bucket missing")
		}
		v := b.Get([]byte(id))
		if v == nil {
			return errors.New("not found")
		}
		return json.Unmarshal(v, &a)
	})
	return a, err
}

func (s *BoltStore) SaveApp(a apps.App) error {
	if s.db == nil {
		return errors.New("db not open")
	}
	data, err := json.Marshal(a)
	if err != nil {
		return err
	}
	return s.db.Update(func(tx *bolt.Tx) error {
		b := tx.Bucket([]byte(appsBucket))
		if b == nil {
			return errors.New("bucket missing")
		}
		return b.Put([]byte(a.ID), data)
	})
}

func (s *BoltStore) DeleteApp(id string) error {
	if s.db == nil {
		return errors.New("db not open")
	}
	return s.db.Update(func(tx *bolt.Tx) error {
		b := tx.Bucket([]byte(appsBucket))
		if b == nil {
			return errors.New("bucket missing")
		}
		return b.Delete([]byte(id))
	})
}
