package system

import (
	"os"
	"os/exec"
)

// DetectGPU detects the GPU vendor on the host system
// Returns "nvidia", "amd", "intel", or "" if no GPU detected
func DetectGPU() string {
	// Check for NVIDIA GPU
	if hasNvidiaGPU() {
		return "nvidia"
	}

	// Check for AMD GPU (ROCm)
	if hasAMDGPU() {
		return "amd"
	}

	// Check for Intel GPU
	if hasIntelGPU() {
		return "intel"
	}

	return "" // No GPU detected
}

// hasNvidiaGPU checks if nvidia-smi is available
func hasNvidiaGPU() bool {
	_, err := exec.LookPath("nvidia-smi")
	return err == nil
}

// hasAMDGPU checks if ROCm is installed
func hasAMDGPU() bool {
	// Check for ROCm installation directory
	if _, err := os.Stat("/opt/rocm"); err == nil {
		return true
	}

	// Check for rocm-smi command
	if _, err := exec.LookPath("rocm-smi"); err == nil {
		return true
	}

	return false
}

// hasIntelGPU checks if Intel GPU tools are available
func hasIntelGPU() bool {
	// Check for intel_gpu_top command
	if _, err := exec.LookPath("intel_gpu_top"); err == nil {
		return true
	}

	// Check for Intel compute runtime
	if _, err := os.Stat("/usr/lib/x86_64-linux-gnu/intel-opencl"); err == nil {
		return true
	}

	return false
}
