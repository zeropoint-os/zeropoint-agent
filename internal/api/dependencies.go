package api

import (
	"fmt"
	"sort"
	"strings"
)

// DependencyGraph represents app dependencies for topological sorting
type DependencyGraph struct {
	nodes    map[string]bool
	edges    map[string][]string // app -> list of dependencies
	incoming map[string]int      // app -> incoming edge count
}

// NewDependencyGraph creates a new dependency graph
func NewDependencyGraph() *DependencyGraph {
	return &DependencyGraph{
		nodes:    make(map[string]bool),
		edges:    make(map[string][]string),
		incoming: make(map[string]int),
	}
}

// AddNode adds an app to the graph
func (g *DependencyGraph) AddNode(app string) {
	if !g.nodes[app] {
		g.nodes[app] = true
		g.edges[app] = []string{}
		g.incoming[app] = 0
	}
}

// AddDependency adds a dependency edge (from depends on to, so to -> from in graph)
func (g *DependencyGraph) AddDependency(from, to string) {
	g.AddNode(from)
	g.AddNode(to)

	// Check if edge already exists (to -> from)
	for _, dep := range g.edges[to] {
		if dep == from {
			return // Edge already exists
		}
	}

	// Add edge: dependency -> dependent (to -> from)
	g.edges[to] = append(g.edges[to], from)
	g.incoming[from]++
}

// TopologicalSort returns apps in dependency order (dependencies first)
// Returns error if circular dependency is detected
func (g *DependencyGraph) TopologicalSort() ([]string, error) {
	// Kahn's algorithm
	result := []string{}
	queue := []string{}

	// Copy incoming counts (we'll modify them)
	incomingCopy := make(map[string]int)
	for app, count := range g.incoming {
		incomingCopy[app] = count
		if count == 0 {
			queue = append(queue, app)
		}
	}

	// Sort queue for deterministic results
	sort.Strings(queue)

	for len(queue) > 0 {
		// Pop from queue
		current := queue[0]
		queue = queue[1:]
		result = append(result, current)

		// Process dependencies
		deps := g.edges[current]
		sort.Strings(deps) // For deterministic order

		for _, dep := range deps {
			incomingCopy[dep]--
			if incomingCopy[dep] == 0 {
				queue = append(queue, dep)
				sort.Strings(queue) // Keep queue sorted
			}
		}
	}

	// Check for cycles
	if len(result) != len(g.nodes) {
		return nil, fmt.Errorf("circular dependency detected")
	}

	return result, nil
}

// AnalyzeDependencies builds a dependency graph from app configurations
func AnalyzeDependencies(apps map[string]map[string]interface{}) (*DependencyGraph, error) {
	graph := NewDependencyGraph()

	// Add all apps as nodes
	for appName := range apps {
		graph.AddNode(appName)
	}

	// Analyze dependencies
	for moduleName, config := range apps {
		for _, value := range config {
			// Check if value is a module reference
			if ref, isRef := parseAppReference(value); isRef {
				// This module depends on the referenced module
				graph.AddDependency(moduleName, ref.FromModule)
			}
		}
	}

	return graph, nil
}

// parseAppReference checks if a value is a module reference and parses it
func parseAppReference(value interface{}) (AppReference, bool) {
	// Try to parse as map[string]interface{} first (explicit reference format)
	if valueMap, ok := value.(map[string]interface{}); ok {
		fromModule, hasFromModule := valueMap["from_module"]
		output, hasOutput := valueMap["output"]

		if hasFromModule && hasOutput {
			fromModuleStr, fromModuleOk := fromModule.(string)
			outputStr, outputOk := output.(string)

			if fromModuleOk && outputOk {
				return AppReference{
					FromModule: fromModuleStr,
					Output:     outputStr,
				}, true
			}
		}
	}

	// Try to parse as string interpolation format: ${module.output}
	if str, ok := value.(string); ok {
		// Match pattern like ${module_name.output_name}
		if len(str) > 3 && str[0:2] == "${" && str[len(str)-1] == '}' {
			// Extract the content between ${ and }
			content := str[2 : len(str)-1]

			// Split on first dot to get module and output
			parts := strings.SplitN(content, ".", 2)
			if len(parts) == 2 {
				return AppReference{
					FromModule: parts[0],
					Output:     parts[1],
				}, true
			}
		}
	}

	return AppReference{}, false
}
