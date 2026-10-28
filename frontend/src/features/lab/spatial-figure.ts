import {
  rectangularSurface,
  ribbonSlices,
  type SpatialGroup,
} from "./spatial-model";
import type { SceneCamera, SceneFigure } from "./spatial-runtime";
export type SpatialTheme = {
  text: string;
  secondary: string;
  border: string;
  surface: string;
  colors: string[];
  font: string;
};
export function spatialFigure(
  group: SpatialGroup,
  options: {
    geometry: "points" | "surface" | "slices";
    opacity: number;
    selectedKey?: string;
    camera?: SceneCamera;
    revision: string;
    theme: SpatialTheme;
  },
): SceneFigure {
  const { theme, camera, revision, opacity } = options;
  const colorscale = theme.colors.map((color, index) => [
    index / (theme.colors.length - 1),
    color,
  ]);
  const hovertemplate = "X: %{x}<br>Y: %{y}<br>Z: %{z}<extra></extra>";
  const heights = group.points.map((point) => point.coords[2]);
  const cmin = Math.min(...heights),
    cmax = Math.max(...heights);
  const colorbar = {
    title: { text: "Z" },
    thickness: 8,
    len: 0.55,
    tickfont: { color: theme.secondary },
  };
  let data: Record<string, unknown>[];
  if (options.geometry === "slices") {
    const ribbons = ribbonSlices(group.points);
    if (!ribbons.ok) throw new Error(ribbons.reason);
    data = ribbons.slices.map((slice, index) => ({
      type: "surface",
      name: `Direction ${slice.y}`,
      x: slice.points.map((point) => point.coords[0]),
      y: [slice.y - ribbons.width / 2, slice.y + ribbons.width / 2],
      z: [
        slice.points.map((point) => point.coords[2]),
        slice.points.map((point) => point.coords[2]),
      ],
      customdata: [
        slice.points.map((point) => point.identity.key),
        slice.points.map((point) => point.identity.key),
      ],
      hovertemplate: "X: %{x}<br>Z: %{z}<extra></extra>",
      opacity,
      colorscale,
      cmin,
      cmax,
      showscale: index === 0,
      colorbar,
      lighting: { ambient: 0.85, diffuse: 0.5, specular: 0.15, roughness: 0.9 },
      connectgaps: false,
    }));
  } else if (options.geometry === "surface") {
    const grid = rectangularSurface(group.points);
    if (!grid.ok) throw new Error(grid.reason);
    data = [
      {
        type: "surface",
        x: grid.x,
        y: grid.y,
        z: grid.z,
        customdata: grid.points.map((row) =>
          row.map((point) => point.identity.key),
        ),
        hovertemplate,
        colorscale,
        opacity,
        showscale: true,
        cmin,
        cmax,
        colorbar,
        contours: {
          z: { show: true, usecolormap: true, project: { z: true } },
        },
        lighting: {
          ambient: 0.85,
          diffuse: 0.5,
          specular: 0.15,
          roughness: 0.9,
        },
        connectgaps: false,
      },
    ];
  } else
    data = [
      {
        type: "scatter3d",
        mode: "markers",
        name: group.label,
        x: group.points.map((point) => point.coords[0]),
        y: group.points.map((point) => point.coords[1]),
        z: group.points.map((point) => point.coords[2]),
        customdata: group.points.map((point) => point.identity.key),
        hovertemplate,
        marker: {
          color: group.points.map((point) => point.coords[2]),
          colorscale,
          cmin,
          cmax,
          showscale: true,
          colorbar,
          size: 4,
          opacity,
          symbol: group.points.map((point) =>
            Object.values(point.estimatedAxes).some(Boolean)
              ? "diamond"
              : "circle",
          ),
          line: { width: 0 },
        },
        showlegend: false,
      },
    ];
  const selected = group.points.find(
    (point) => point.identity.key === options.selectedKey,
  );
  if (selected)
    data.push({
      type: "scatter3d",
      mode: "markers",
      name: "Pinned record",
      x: [selected.coords[0]],
      y: [selected.coords[1]],
      z: [selected.coords[2]],
      customdata: [selected.identity.key],
      marker: {
        size: 7,
        color: theme.text,
        symbol: "circle-open",
        line: { width: 2, color: theme.text },
      },
      hovertemplate,
      showlegend: false,
    });
  const axis = (name: "x" | "y" | "z") => ({
    title: {
      text: group.axes[name].label,
      font: { size: 12, color: theme.secondary },
    },
    color: theme.secondary,
    gridcolor: theme.border,
    zerolinecolor: theme.border,
    showbackground: true,
    backgroundcolor: theme.surface,
    tickfont: { size: 11 },
    exponentformat: "SI",
    showspikes: false,
  });
  return {
    data,
    layout: {
      autosize: true,
      margin: { l: 0, r: 0, t: 0, b: 0 },
      showlegend: false,
      font: { family: theme.font, color: theme.text },
      paper_bgcolor: theme.surface,
      hoverlabel: {
        bgcolor: theme.surface,
        bordercolor: theme.border,
        font: { color: theme.text, family: theme.font },
      },
      scene: {
        xaxis: axis("x"),
        yaxis: axis("y"),
        zaxis: axis("z"),
        aspectmode: "cube",
        dragmode: "orbit",
        uirevision: revision,
        camera: camera ?? {
          eye: { x: 1.7, y: 1.7, z: 1.2 },
          up: { x: 0, y: 0, z: 1 },
        },
        bgcolor: theme.surface,
      },
      uirevision: revision,
    },
  };
}
