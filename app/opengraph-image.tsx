import { ImageResponse } from "next/og";

export const alt = "FOMC Basis Monitor — cross-market research";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function Image() {
  return new ImageResponse(
    <div style={{ width: "100%", height: "100%", background: "#f3f1eb", color: "#17211b", display: "flex", flexDirection: "column", padding: "72px 84px", justifyContent: "space-between", fontFamily: "Arial" }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 24, letterSpacing: 2, textTransform: "uppercase" }}>
        <span>FOMC Basis Monitor</span><span>Research only</span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 26 }}>
        <div style={{ fontSize: 72, lineHeight: 1.03, maxWidth: 960 }}>Is the apparent probability gap executable?</div>
        <div style={{ fontSize: 30, color: "#536058" }}>Kalshi × 30-Day Fed Funds futures</div>
      </div>
      <div style={{ display: "flex", gap: 20, fontSize: 22 }}>
        <span style={{ border: "2px solid #a9513d", padding: "12px 20px", color: "#8d3f2d" }}>Hard-gate classification</span>
        <span style={{ border: "2px solid #7d857f", padding: "12px 20px" }}>Python model</span>
        <span style={{ border: "2px solid #7d857f", padding: "12px 20px" }}>Input provenance</span>
      </div>
    </div>,
    size,
  );
}
