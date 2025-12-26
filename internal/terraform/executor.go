package terraform

import (
	"context"
	"encoding/json"
	"fmt"
	"os/exec"

	"github.com/hashicorp/terraform-exec/tfexec"
)

// Executor wraps the Terraform executor
type Executor struct {
	tf         *tfexec.Terraform
	workingDir string
}

// OutputMeta represents metadata about a Terraform output
type OutputMeta struct {
	Sensitive bool
	Type      interface{}
	Value     interface{}
}

// NewExecutor creates a new Terraform executor for the given module path
func NewExecutor(modulePath string) (*Executor, error) {
	// Find terraform binary
	terraformPath, err := exec.LookPath("terraform")
	if err != nil {
		return nil, fmt.Errorf("terraform binary not found: %w", err)
	}

	tf, err := tfexec.NewTerraform(modulePath, terraformPath)
	if err != nil {
		return nil, err
	}

	return &Executor{
		tf:         tf,
		workingDir: modulePath,
	}, nil
}

// Init runs terraform init
func (e *Executor) Init() error {
	return e.tf.Init(context.Background())
}

// Plan runs terraform plan
func (e *Executor) Plan(outFile string, variables map[string]string) error {
	opts := []tfexec.PlanOption{
		tfexec.Out(outFile),
	}

	// Add each variable as a separate Var option
	for k, v := range variables {
		opts = append(opts, tfexec.Var(k+"="+v))
	}

	// Run plan and check for errors
	hasChanges, err := e.tf.Plan(context.Background(), opts...)
	if err != nil {
		return fmt.Errorf("terraform plan failed: %w", err)
	}

	// Log if there are changes (for debugging)
	_ = hasChanges

	return nil
}

// Apply runs terraform apply
func (e *Executor) Apply(variables map[string]string) error {
	opts := []tfexec.ApplyOption{}

	for k, v := range variables {
		opts = append(opts, tfexec.Var(k+"="+v))
	}

	return e.tf.Apply(context.Background(), opts...)
}

// Destroy runs terraform destroy
func (e *Executor) Destroy(variables map[string]string) error {
	opts := []tfexec.DestroyOption{}

	for k, v := range variables {
		opts = append(opts, tfexec.Var(k+"="+v))
	}

	return e.tf.Destroy(context.Background(), opts...)
}

// Output reads terraform outputs
func (e *Executor) Output() (map[string]*OutputMeta, error) {
	outputs, err := e.tf.Output(context.Background())
	if err != nil {
		return nil, err
	}

	result := make(map[string]*OutputMeta)
	for k, v := range outputs {
		result[k] = &OutputMeta{
			Sensitive: v.Sensitive,
			Type:      v.Type,
			Value:     v.Value,
		}
	}

	return result, nil
}

// Show reads the terraform plan JSON
func (e *Executor) Show(planFile string) ([]byte, error) {
	// Use ShowPlanFile to get the plan as a struct, then marshal to JSON
	plan, err := e.tf.ShowPlanFile(context.Background(), planFile)
	if err != nil {
		return nil, fmt.Errorf("ShowPlanFile failed for %s: %w", planFile, err)
	}

	// Marshal the plan struct to JSON
	planJSON, err := json.Marshal(plan)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal plan to JSON: %w", err)
	}

	return planJSON, nil
}
