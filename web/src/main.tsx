import React from "react";
import ReactDOM from "react-dom/client";
import { CssBaseline, GlobalStyles, ThemeProvider } from "@mui/material";
import { theme } from "./theme";
import Root from "./Root";
import { useLang } from "./i18n";

function LangRoot() { const l = useLang(); return <Root key={l} />; }

/** A failing screen shows what happened and a way back, never a blank page. */
class Boundary extends React.Component<{ children: React.ReactNode }, { err: Error | null }> {
  state = { err: null as Error | null };
  static getDerivedStateFromError(err: Error) { return { err }; }
  componentDidCatch(err: Error) { console.error(err); }
  render() {
    if (!this.state.err) return this.props.children;
    return (
      <div style={{ fontFamily: "Inter, system-ui, sans-serif", padding: 40, maxWidth: 720, margin: "0 auto" }}>
        <h2 style={{ margin: "0 0 8px" }}>Something went wrong on this screen</h2>
        <p style={{ color: "#646B76", margin: "0 0 16px" }}>{this.state.err.message}</p>
        <button onClick={() => window.location.reload()} style={{ padding: "8px 16px", marginRight: 8 }}>Reload</button>
        <button onClick={() => { window.location.hash = "/app"; window.location.reload(); }} style={{ padding: "8px 16px" }}>Back to conversions</button>
      </div>
    );
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      {/* direction arrows point the reading way in Arabic */}
      <GlobalStyles styles={{ 'html[dir="rtl"] [data-testid^="ArrowForward"], html[dir="rtl"] [data-testid^="ArrowBack"], html[dir="rtl"] [data-testid^="East"], html[dir="rtl"] [data-testid^="ChevronRight"], html[dir="rtl"] [data-testid^="ChevronLeft"]': { transform: "scaleX(-1)" } }} />
      <Boundary><LangRoot /></Boundary>
    </ThemeProvider>
  </React.StrictMode>,
);
