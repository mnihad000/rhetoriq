import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SourceText } from "./InvestigationWorkspace";

describe("SourceText", () => {
  it("highlights a valid search span without trusting span text for indexing", () => {
    render(<SourceText text="prefix highlighted suffix" span={{ start: 7, end: 18, text: "highlighted" }} />);
    expect(screen.getByRole("paragraph", { name: /highlighted span/i })).toBeInTheDocument();
    expect(screen.getByText("highlighted").tagName).toBe("MARK");
  });

  it("renders safely when offsets are outside the stored text", () => {
    render(<SourceText text="short source" span={{ start: 99, end: 120, text: "missing" }} />);
    expect(screen.queryByText("missing", { selector: "mark" })).not.toBeInTheDocument();
    expect(screen.getByText("short source")).toBeInTheDocument();
  });
});
