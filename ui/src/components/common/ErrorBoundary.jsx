// Catches render-time crashes in the subtree and shows the error instead of
// letting React unmount everything (which looks like a black screen). React
// only supports error boundaries as CLASS components — there is no hook
// equivalent for componentDidCatch — so this one file is intentionally a class.
import React from "react";

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Kept for future crash reporting; console is enough while developing.
    console.error("UI crash:", error, info);
  }

  reset = () => this.setState({ error: null });

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="empty-state" style={{ height: "100%" }}>
        <h3 style={{ color: "var(--red)" }}>This screen hit an error</h3>
        <p style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--mid)" }}>
          {String(this.state.error?.message || this.state.error)}
        </p>
        <button className="btn primary" onClick={this.reset}>Try again</button>
      </div>
    );
  }
}
