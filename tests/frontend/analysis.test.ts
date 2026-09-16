import { describe, expect, it } from "vitest";
import { reasonText, reasonCopy } from "../../lib/analysis";

describe("classification reason rendering", () => {
  it("renders every hard gate as specific professional copy", () => {
    expect(reasonText("NON_EXECUTABLE_QUOTES")).toBe("Futures quote is indicative rather than executable.");
    expect(reasonText("UNSYNCHRONIZED_QUOTES")).toContain("synchronization window");
    expect(reasonText("SETTLEMENT_MISMATCH")).toContain("Settlement definitions");
    expect(reasonText("INSUFFICIENT_DEPTH")).toContain("depth");
    expect(reasonText("LOSING_BASIS_STRESS")).toContain("Basis stress");
  });

  it("retains unknown reason codes without hiding them", () => {
    expect(reasonText("NEW_CONSERVATIVE_GATE")).toBe("New conservative gate.");
    expect(Object.keys(reasonCopy).length).toBeGreaterThan(8);
  });
});
