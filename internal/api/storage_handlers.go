package api

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os/exec"

	"github.com/gorilla/mux"
)

// Disk represents a block device exposed by the API
//
// swagger:model Disk
type Disk struct {
	DiskID     string      `json:"disk_id"`
	SysPath    string      `json:"sys_path"`
	KName      string      `json:"kname"`
	Model      string      `json:"model,omitempty"`
	Serial     string      `json:"serial,omitempty"`
	WWN        string      `json:"wwn,omitempty"`
	Size       uint64      `json:"size_bytes,omitempty"`
	Rotational bool        `json:"rotational,omitempty"`
	Transport  string      `json:"transport,omitempty"`
	Vendor     string      `json:"vendor,omitempty"`
	Partitions []Partition `json:"partitions,omitempty"`
}

// Partition contains basic partition info
//
// swagger:model Partition
type Partition struct {
	PartitionID string `json:"partition_id"`
	KName       string `json:"kname"`
	SysPath     string `json:"sys_path"`
	Index       int    `json:"index"`
	Size        uint64 `json:"size_bytes,omitempty"`
	FsType      string `json:"fs_type,omitempty"`
	UUID        string `json:"uuid,omitempty"`
	Label       string `json:"label,omitempty"`
}

// lsblkJSON mirrors the minimal structure returned by `lsblk -J`
type lsblkOut struct {
	Blockdevices []map[string]interface{} `json:"blockdevices"`
}

// ListDisks handles GET /api/storage/disks
//
// @Summary List block devices
// @Description Returns block devices detected on the system
// @Tags storage
// @Produce json
// @Success 200 {array} api.Disk
// @Failure 500 {object} map[string]string
// @Router /api/storage/disks [get]
func (e *apiEnv) ListDisks(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	disks, err := enumerateDisks(ctx)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(disks)
}

// GetDisk handles GET /api/storage/disks/{disk}
//
// @Summary Get a single block device
// @Description Returns detailed metadata for a single disk
// @Tags storage
// @Produce json
// @Param disk path string true "Disk ID (kname or disk_id)"
// @Success 200 {object} api.Disk
// @Failure 404 {object} map[string]string
// @Failure 500 {object} map[string]string
// @Router /api/storage/disks/{disk} [get]
func (e *apiEnv) GetDisk(w http.ResponseWriter, r *http.Request) {
	vars := mux.Vars(r)
	id := vars["disk"]
	ctx := r.Context()
	disks, err := enumerateDisks(ctx)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	for _, d := range disks {
		if d.KName == id || d.DiskID == id {
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(d)
			return
		}
	}
	http.Error(w, "disk not found", http.StatusNotFound)
}

// enumerateDisks calls lsblk and parses minimal device info
func enumerateDisks(ctx context.Context) ([]Disk, error) {
	cmd := exec.CommandContext(ctx, "lsblk", "-J", "-b", "-o", "NAME,KNAME,PATH,SIZE,MODEL,SERIAL,WWN,UUID,TYPE,ROTA,TRAN,VENDOR")
	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("lsblk failed: %w", err)
	}
	var l lsblkOut
	if err := json.Unmarshal(out, &l); err != nil {
		return nil, fmt.Errorf("failed to parse lsblk output: %w", err)
	}

	var disks []Disk
	for _, dev := range l.Blockdevices {
		if t, ok := dev["type"].(string); ok && t == "disk" {
			d := Disk{}
			if k, ok := dev["kname"].(string); ok {
				d.KName = k
				d.DiskID = k
				d.SysPath = "/dev/" + k
			}
			if m, ok := dev["model"].(string); ok {
				d.Model = m
			}
			if s, ok := dev["serial"].(string); ok {
				d.Serial = s
			}
			if w, ok := dev["wwn"].(string); ok {
				d.WWN = w
			}
			if v, ok := dev["vendor"].(string); ok {
				d.Vendor = v
			}
			if tr, ok := dev["tran"].(string); ok {
				d.Transport = tr
			}
			if rota, ok := dev["rota"].(string); ok {
				d.Rotational = (rota == "1")
			}
			if sz, ok := dev["size"].(float64); ok {
				d.Size = uint64(sz)
			}
			// partitions
			if children, ok := dev["children"].([]interface{}); ok {
				for idx, c := range children {
					if cm, ok := c.(map[string]interface{}); ok {
						p := Partition{Index: idx + 1}
						if k, ok := cm["kname"].(string); ok {
							p.KName = k
							p.PartitionID = k
							p.SysPath = "/dev/" + k
						}
						if sz, ok := cm["size"].(float64); ok {
							p.Size = uint64(sz)
						}
						if t, ok := cm["type"].(string); ok {
							_ = t
						}
						if fs, ok := cm["uuid"].(string); ok {
							p.UUID = fs
						}
						if fstype, ok := cm["fstype"].(string); ok {
							p.FsType = fstype
						}
						if label, ok := cm["label"].(string); ok {
							p.Label = label
						}
						d.Partitions = append(d.Partitions, p)
					}
				}
			}
			disks = append(disks, d)
		}
	}
	return disks, nil
}
