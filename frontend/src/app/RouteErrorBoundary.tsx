import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button, Page, Panel } from "../components/studio/primitives";
import { RouteModuleError } from "./lazyRoute";
interface Props {
  children: ReactNode;
  resetKey: string;
  onError?: (error: Error, info: ErrorInfo) => void;
  onReload?: () => void;
}
interface State {
  error: Error | null;
  attempt: number;
}
/** A page render failure is contained below navigation and session ownership. */
export class RouteErrorBoundary extends Component<Props, State> {
  state: State = { error: null, attempt: 0 };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    this.props.onError?.(error, info);
  }
  componentDidUpdate(previous: Props) {
    if (previous.resetKey !== this.props.resetKey && this.state.error)
      this.setState({ error: null, attempt: this.state.attempt + 1 });
  }
  private retry = () =>
    this.setState(({ attempt }) => ({ error: null, attempt: attempt + 1 }));
  render() {
    if (this.state.error) {
      const moduleFailed = this.state.error instanceof RouteModuleError;
      return (
        <Page
          title={
            moduleFailed
              ? "This page could not load"
              : "This page could not render"
          }
          description="Your workspace is still open. You can choose another page from navigation."
        >
          <Panel>
            <p role="alert">
              {moduleFailed
                ? "Page code could not be downloaded. Check your connection, then reload this page. Reloading clears unsaved input."
                : "An unexpected interface error interrupted this page."}
            </p>
            {moduleFailed ? (
              <a
                href={window.location.href}
                onClick={(event) => {
                  event.preventDefault();
                  if (this.props.onReload) this.props.onReload();
                  else window.location.reload();
                }}
              >
                Reload this page
              </a>
            ) : (
              <Button variant="primary" onClick={this.retry}>
                Try this page again
              </Button>
            )}
            <a href="#/datasets" style={{ marginInlineStart: "1rem" }}>
              Open datasets
            </a>
          </Panel>
        </Page>
      );
    }
    return (
      <div key={this.state.attempt} className="contents">
        {this.props.children}
      </div>
    );
  }
}
