package hcl

import (
	"fmt"
	"path/filepath"

	"github.com/hashicorp/hcl/v2"
	"github.com/hashicorp/hcl/v2/hclparse"
	"github.com/hashicorp/hcl/v2/hclsyntax"
	"github.com/zclconf/go-cty/cty"
	"github.com/zclconf/go-cty/cty/gocty"
)

// Output represents a parsed HCL output block
type Output struct {
	Name        string
	Value       interface{}
	Description string
}

// ParseModuleOutputs parses main.tf and extracts all output blocks
func ParseModuleOutputs(modulePath string) (map[string]Output, error) {
	mainTfPath := filepath.Join(modulePath, "main.tf")

	parser := hclparse.NewParser()
	file, diags := parser.ParseHCLFile(mainTfPath)
	if diags.HasErrors() {
		return nil, fmt.Errorf("failed to parse HCL: %s", diags.Error())
	}

	// Type assert to get the actual syntax body
	body, ok := file.Body.(*hclsyntax.Body)
	if !ok {
		return nil, fmt.Errorf("unexpected body type: %T", file.Body)
	}

	outputs := make(map[string]Output)

	// Iterate through top-level blocks to find outputs
	for _, block := range body.Blocks {
		if block.Type != "output" {
			continue
		}

		if len(block.Labels) == 0 {
			continue
		}

		outputName := block.Labels[0]
		output := Output{Name: outputName}

		// Extract value and description attributes
		attrs, diags := block.Body.JustAttributes()
		if diags.HasErrors() {
			return nil, fmt.Errorf("failed to get attributes for output %s: %s", outputName, diags.Error())
		}

		// Parse value attribute
		if valueAttr, ok := attrs["value"]; ok {
			val, err := evaluateExpression(valueAttr.Expr)
			if err != nil {
				// If we can't evaluate (e.g., references to resources), set to nil
				// This is OK for outputs like "main" that reference docker_container resources
				// We only need static values for ports/mounts validation
				output.Value = nil
			} else {
				output.Value = val
			}
		}

		// Parse description attribute
		if descAttr, ok := attrs["description"]; ok {
			val, diags := descAttr.Expr.Value(nil)
			if !diags.HasErrors() && val.Type() == cty.String {
				output.Description = val.AsString()
			}
		}

		outputs[outputName] = output
	}

	return outputs, nil
}

// evaluateExpression attempts to evaluate an HCL expression to a Go value
// This handles static values (maps, lists, strings, numbers, bools)
func evaluateExpression(expr hcl.Expression) (interface{}, error) {
	// Try to evaluate without context (works for static values)
	val, diags := expr.Value(nil)
	if diags.HasErrors() {
		// If evaluation fails, it might contain references (e.g., docker_container.main)
		// For validation purposes, we can extract the structure even without evaluation
		return extractStructure(expr)
	}

	return ctyToGo(val)
}

// extractStructure extracts the structure of an expression without evaluating references
func extractStructure(expr hcl.Expression) (interface{}, error) {
	switch e := expr.(type) {
	case *hclsyntax.ObjectConsExpr:
		// This is a map literal like { key = value }
		result := make(map[string]interface{})
		for _, item := range e.Items {
			// Extract key
			keyVal, diags := item.KeyExpr.Value(nil)
			if diags.HasErrors() {
				return nil, fmt.Errorf("failed to evaluate object key: %s", diags.Error())
			}
			if keyVal.Type() != cty.String {
				return nil, fmt.Errorf("object keys must be strings")
			}
			key := keyVal.AsString()

			// Extract value recursively
			val, err := evaluateExpression(item.ValueExpr)
			if err != nil {
				return nil, fmt.Errorf("failed to evaluate value for key %s: %w", key, err)
			}
			result[key] = val
		}
		return result, nil

	case *hclsyntax.TupleConsExpr:
		// This is a list literal like [1, 2, 3]
		result := make([]interface{}, len(e.Exprs))
		for i, itemExpr := range e.Exprs {
			val, err := evaluateExpression(itemExpr)
			if err != nil {
				return nil, fmt.Errorf("failed to evaluate list item %d: %w", i, err)
			}
			result[i] = val
		}
		return result, nil

	default:
		// For other expressions (references, function calls, etc.),
		// try to evaluate with nil context
		val, diags := expr.Value(nil)
		if diags.HasErrors() {
			return nil, fmt.Errorf("cannot extract structure from expression: %s", diags.Error())
		}
		return ctyToGo(val)
	}
}

// ctyToGo converts a cty.Value to a Go interface{} value
func ctyToGo(val cty.Value) (interface{}, error) {
	if val.IsNull() {
		return nil, nil
	}

	valType := val.Type()

	switch {
	case valType == cty.String:
		return val.AsString(), nil
	case valType == cty.Number:
		var f float64
		if err := gocty.FromCtyValue(val, &f); err != nil {
			return nil, err
		}
		// Check if it's actually an integer
		if f == float64(int(f)) {
			return int(f), nil
		}
		return f, nil
	case valType == cty.Bool:
		return val.True(), nil
	case valType.IsMapType() || valType.IsObjectType():
		result := make(map[string]interface{})
		it := val.ElementIterator()
		for it.Next() {
			k, v := it.Element()
			key := k.AsString()
			goVal, err := ctyToGo(v)
			if err != nil {
				return nil, err
			}
			result[key] = goVal
		}
		return result, nil
	case valType.IsListType() || valType.IsTupleType() || valType.IsSetType():
		result := make([]interface{}, 0, val.LengthInt())
		it := val.ElementIterator()
		for it.Next() {
			_, v := it.Element()
			goVal, err := ctyToGo(v)
			if err != nil {
				return nil, err
			}
			result = append(result, goVal)
		}
		return result, nil
	default:
		return nil, fmt.Errorf("unsupported cty type: %s", valType.FriendlyName())
	}
}
