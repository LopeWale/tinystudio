import { useMemo, useState, useEffect } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  applyNodeChanges,
  type Node,
  type Edge,
  type NodeChange,
  type Connection,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Input } from "@/components/boardui/base/input/input";
import { Textarea } from "@/components/boardui/base/textarea/textarea";
import { Button, EmptyState } from "../../components/studio/primitives";
import { LabChoice } from "./inputs";
import {
  inputExpectations,
  type ExperimentDefinition,
  type LabComponent,
  type LabConnection,
} from "./definition";
const kinds = ["signal", "plant", "controller", "sensor", "transport"];
const defaults: Record<string, Record<string, unknown>> = {
  signal: { waveform: "constant", amplitude: 1 },
  plant: { time_constant: 1, gain: 1, initial_state: 0 },
  controller: { gain: 1, setpoint: 1 },
  sensor: { bias: 0, noise_std: 0 },
  transport: { delay_steps: 1, initial_value: 0 },
};
export function componentPorts(kind?: string): string[] {
  return kind === "controller"
    ? ["reference", "measurement"]
    : ["plant", "sensor", "transport"].includes(kind ?? "")
      ? ["input"]
      : [];
}
function ScalarNode({
  data,
}: NodeProps<Node<{ label: string; kind: string; ports: string[] }>>) {
  return (
    <div className="lab-component-card">
      <strong>{data.label}</strong>
      <small>{data.kind} · scalar output</small>
      {data.ports.map((port, index) => (
        <Handle
          key={port}
          id={port}
          type="target"
          position={Position.Left}
          style={{ top: 32 + index * 24 }}
          aria-label={`${port} input`}
        />
      ))}
      <Handle
        id="output"
        type="source"
        position={Position.Right}
        aria-label="Scalar output"
      />
      {data.ports.map((port) => (
        <small key={port}>{port}</small>
      ))}
    </div>
  );
}
const nodeTypes = { scalar: ScalarNode };
export function toGraph(definition: ExperimentDefinition): {
  nodes: Node[];
  edges: Edge[];
} {
  return {
    nodes: definition.components
      .filter((c) => c && typeof c.id === "string")
      .map((c, index) => ({
        id: c.id,
        type: "scalar",
        position: c.position ?? {
          x: (index % 3) * 260,
          y: Math.floor(index / 3) * 180,
        },
        data: {
          label: c.label ?? c.id,
          kind: c.kind ?? c.type ?? "Unspecified",
          ports: componentPorts(c.kind),
        },
      })),
    edges: definition.connections
      .filter(
        (c) =>
          c && typeof c.source === "string" && typeof c.target === "string",
      )
      .map((link, index) => ({
        id: link.id ?? `connection-${index}`,
        source: link.source,
        target: link.target,
        sourceHandle: "output",
        targetHandle: link.port ?? link.target_port ?? "input",
        label: link.port ?? link.target_port ?? link.kind ?? "input",
      })),
  };
}
export function DefinitionGraph({
  definition,
  onChange,
  readOnly = false,
}: {
  definition: ExperimentDefinition;
  onChange: (patch: Partial<ExperimentDefinition>) => void;
  readOnly?: boolean;
}) {
  const graph = useMemo(() => toGraph(definition), [definition]);
  const editable = !readOnly && definition.engine === "system.cosim";
  const components = definition.components;
  const [name, setName] = useState("");
  const [kind, setKind] = useState("signal");
  const [selected, setSelected] = useState("");
  const [source, setSource] = useState(components[0]?.id ?? "");
  const [target, setTarget] = useState(
    components.find((c) => componentPorts(c.kind).length)?.id ?? "",
  );
  const [port, setPort] = useState("input");
  const [parameters, setParameters] = useState("{}");
  const [fault, setFault] = useState("");
  const component = components.find((c) => c.id === selected);
  useEffect(() => {
    setParameters(JSON.stringify(component?.parameters ?? {}, null, 2));
    setFault("");
  }, [selected, component?.kind]);
  const updateComponent = (patch: Partial<LabComponent>) =>
    onChange({
      components: components.map((c) =>
        c.id === selected ? { ...c, ...patch } : c,
      ),
    });
  const ports = componentPorts(components.find((c) => c.id === target)?.kind);
  const effectivePort = ports.includes(port) ? port : (ports[0] ?? "");
  const connect = (connection: Connection) => {
    if (!editable || !connection.source || !connection.target) return;
    const targetPort = connection.targetHandle ?? "input";
    if (
      !componentPorts(
        components.find((c) => c.id === connection.target)?.kind,
      ).includes(targetPort)
    ) {
      setFault("Select a declared target input port.");
      return;
    }
    if (
      definition.connections.some(
        (c) =>
          c.target === connection.target &&
          (c.port ?? c.target_port ?? "input") === targetPort,
      )
    ) {
      setFault(
        "This input port is already wired. Remove its connection before replacing it.",
      );
      return;
    }
    setFault("");
    onChange({
      connections: [
        ...definition.connections,
        {
          id: crypto.randomUUID(),
          source: connection.source,
          target: connection.target,
          port: targetPort,
          version: "1",
        },
      ],
    });
  };
  const updateNodes = (changes: NodeChange[]) => {
    if (!editable) return;
    const relevant = changes.filter(
      (c) => c.type === "position" || c.type === "remove",
    );
    if (!relevant.length) return;
    const updated = applyNodeChanges(relevant, graph.nodes);
    const removed = new Set(
      relevant.filter((c) => c.type === "remove").map((c) => c.id),
    );
    onChange({
      components: components
        .filter((c) => !removed.has(c.id))
        .map((c) => ({
          ...c,
          position: updated.find((n) => n.id === c.id)?.position ?? c.position,
        })),
      connections: definition.connections.filter(
        (c) => !removed.has(c.source) && !removed.has(c.target),
      ),
    });
  };
  const removeEdge = (id: string, index: number) =>
    onChange({
      connections: definition.connections.filter(
        (c, i) => (c.id ?? `connection-${i}`) !== (id ?? `connection-${index}`),
      ),
    });
  return (
    <section className="lab-graph-editor">
      <h3>Component topology</h3>
      <p>
        {definition.engine === "system.cosim"
          ? "Executable scalar co-simulation: version 1 kinds and named input ports. Plant state and positive transport delay break direct feedthrough; other algebraic loops are rejected by the engine."
          : "This adapter executes its parameters and immutable input roles. A generic topology is retained reference only and does not execute hardware or replace the adapter schema."}
      </p>
      {definition.engine !== "system.cosim" && (
        <ul>
          {inputExpectations(definition).map((item) => (
            <li key={item.role}>
              <code>{item.role}</code> · {item.description}
            </li>
          ))}
        </ul>
      )}
      <div className="lab-graph-toolbar" hidden={!editable}>
        {editable && (
          <>
            <div className="lab-fields">
              <Input
                label="Master step (seconds)"
                type="number"
                value={String(definition.clocks?.step ?? 0.1)}
                onChange={(value) => {
                  if (Number.isFinite(Number(value)) && Number(value) > 0)
                    onChange({
                      clocks: { ...definition.clocks, step: Number(value) },
                    });
                }}
              />
              <Input
                label="Clock steps"
                type="number"
                value={String(definition.clocks?.steps ?? 100)}
                onChange={(value) => {
                  if (Number.isInteger(Number(value)) && Number(value) > 0)
                    onChange({
                      clocks: { ...definition.clocks, steps: Number(value) },
                    });
                }}
              />
            </div>
            <p>
              Hold: zero_order. Component periods must be integer multiples of
              the master step. Clock tolerance concerns alignment and causal
              availability.
            </p>
            <div className="lab-actions">
              <Input label="Component label" value={name} onChange={setName} />
              <LabChoice
                label="Component kind"
                value={kind}
                onChange={setKind}
                options={kinds.map((id) => ({ id, name: id }))}
              />
              <Button
                disabled={!name.trim()}
                onClick={() => {
                  const id = crypto.randomUUID();
                  onChange({
                    components: [
                      ...components,
                      {
                        id,
                        kind,
                        version: "1",
                        label: name.trim(),
                        parameters: { ...defaults[kind] },
                        position: {
                          x: (components.length % 3) * 260,
                          y: Math.floor(components.length / 3) * 180,
                        },
                      },
                    ],
                  });
                  setSelected(id);
                  setName("");
                }}
              >
                Add component
              </Button>
            </div>
          </>
        )}
      </div>
      <div
        className={
          editable
            ? "lab-topology-workspace"
            : "lab-topology-workspace lab-topology-reference"
        }
      >
        <div className="lab-topology-canvas">
          {!graph.nodes.length && (
            <EmptyState>
              {editable
                ? "Add a supported component, then wire its named ports using the graph or keyboard controls below."
                : "No retained component topology. Use the adapter input contract and parameter editor."}
            </EmptyState>
          )}
          {(editable || graph.nodes.length > 0) && (
            <div className="lab-graph" aria-label="Experiment component graph">
              <ReactFlow
                nodeTypes={nodeTypes}
                nodes={graph.nodes.map((node) => ({
                  ...node,
                  selected: node.id === selected,
                }))}
                edges={graph.edges}
                nodesDraggable={editable}
                nodesConnectable={editable}
                deleteKeyCode={editable ? ["Backspace", "Delete"] : null}
                onNodesChange={updateNodes}
                onConnect={connect}
                onNodeClick={(_, node) => setSelected(node.id)}
                onEdgesDelete={
                  editable
                    ? (edges) => {
                        const ids = new Set(edges.map((e) => e.id));
                        onChange({
                          connections: definition.connections.filter(
                            (c, i) => !ids.has(c.id ?? `connection-${i}`),
                          ),
                        });
                      }
                    : undefined
                }
                fitView
                colorMode="system"
              >
                <Background />
                <Controls />
              </ReactFlow>
            </div>
          )}
        </div>
        {editable && (
          <aside
            className="lab-node-inspector"
            aria-label="Component inspector"
          >
            <h3>
              {component
                ? (component.label ?? component.id)
                : "Component inspector"}
            </h3>
            <LabChoice
              label="Inspect component"
              value={selected}
              onChange={setSelected}
              options={components.map((c) => ({
                id: c.id,
                name: c.label ?? c.id,
              }))}
            />
            {component && editable && (
              <div className="lab-fields">
                <Input
                  label="Selected component label"
                  value={component.label ?? component.id}
                  onChange={(label) => updateComponent({ label })}
                />
                <LabChoice
                  label="Selected component kind"
                  value={component.kind ?? "unspecified"}
                  onChange={(next) => {
                    updateComponent({
                      kind: next,
                      version: "1",
                      parameters: { ...defaults[next] },
                    });
                  }}
                  options={kinds.map((id) => ({ id, name: id }))}
                />
                <Input
                  label="Component period (seconds)"
                  type="number"
                  value={String(
                    component.period ?? definition.clocks?.step ?? 0.1,
                  )}
                  onChange={(value) => {
                    if (Number(value) > 0)
                      updateComponent({ period: Number(value) });
                  }}
                />
                <p>
                  Ports:{" "}
                  {componentPorts(component.kind).join(", ") || "no input"} →
                  scalar output. Units:{" "}
                  {String(component.parameters?.units ?? "unitless")}.
                </p>
                <Textarea
                  label="Component parameters JSON"
                  value={parameters}
                  onChange={setParameters}
                  rows={5}
                />
                <Button
                  onClick={() => {
                    try {
                      const value = JSON.parse(parameters);
                      if (
                        !value ||
                        typeof value !== "object" ||
                        Array.isArray(value)
                      )
                        throw new Error(
                          "Component parameters must be an object.",
                        );
                      updateComponent({ parameters: value });
                      setFault("");
                    } catch (error) {
                      setFault((error as Error).message);
                    }
                  }}
                >
                  Apply component parameters
                </Button>
                <Button
                  onClick={() => {
                    onChange({
                      components: components.filter((c) => c.id !== selected),
                      connections: definition.connections.filter(
                        (c) => c.source !== selected && c.target !== selected,
                      ),
                    });
                    setSelected("");
                  }}
                >
                  Remove selected component
                </Button>
              </div>
            )}
            <details className="lab-wiring-controls" open>
              <summary>Named port connections</summary>
              <div className="lab-fields">
                <LabChoice
                  label="Connection source"
                  value={source}
                  onChange={setSource}
                  options={components.map((c) => ({
                    id: c.id,
                    name: c.label ?? c.id,
                  }))}
                />
                <LabChoice
                  label="Connection target"
                  value={target}
                  onChange={setTarget}
                  options={components
                    .filter((c) => componentPorts(c.kind).length)
                    .map((c) => ({ id: c.id, name: c.label ?? c.id }))}
                />
                <LabChoice
                  label="Target input port"
                  value={effectivePort}
                  onChange={setPort}
                  options={ports.map((id) => ({ id, name: id }))}
                />
                <Button
                  disabled={
                    !components.some((c) => c.id === source) ||
                    !target ||
                    !effectivePort
                  }
                  onClick={() =>
                    connect({
                      source,
                      target,
                      sourceHandle: "output",
                      targetHandle: effectivePort,
                    })
                  }
                >
                  Wire selected ports
                </Button>
              </div>
              <ul className="lab-wiring-list">
                {definition.connections.map((connection, index) => (
                  <li key={connection.id ?? index}>
                    <code>
                      {connection.source} → {connection.target}.
                      {connection.port ?? connection.target_port ?? "input"}
                    </code>
                    <Button
                      onClick={() =>
                        removeEdge(
                          connection.id ?? `connection-${index}`,
                          index,
                        )
                      }
                    >
                      Remove connection {index + 1}
                    </Button>
                  </li>
                ))}
              </ul>
            </details>
          </aside>
        )}
      </div>
      {fault && <p role="alert">{fault}</p>}
    </section>
  );
}
