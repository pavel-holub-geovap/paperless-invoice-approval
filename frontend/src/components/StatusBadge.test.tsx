import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it("renders a readable state", () => {
    render(<StatusBadge value="AWAITING_APPROVAL" />);
    expect(screen.getByText("AWAITING APPROVAL")).toBeInTheDocument();
  });
});

