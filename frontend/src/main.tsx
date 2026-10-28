import { installVisualTruthPatches } from './visual-truth.generated'
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app/App";
import { DirectionProvider } from "./components/boardui/foundations/direction/direction";
import "./styles/globals.css";
import "./styles/studio.css";
import "./styles/workspace.css";
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <DirectionProvider locale="en-US">
      <App />
    </DirectionProvider>
  </StrictMode>,
);
import "./styles/workbench.css";


installVisualTruthPatches()
