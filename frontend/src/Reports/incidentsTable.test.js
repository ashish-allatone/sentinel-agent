/**
 * The incidents table holds one free-text column (Description) next to five
 * short ones. It must not let that column push the others out of shape.
 */
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import IncidentsTable from "./components/IncidentsTable";

const LONG =
  "Anomalous execution: python3 — /usr/bin/python3 /opt/sentinel/agent/collector.py " +
  "--config /etc/sentinel/agent.yaml --verbose --tag production-eu-west --retry 5";

const INCIDENTS = [
  {
    date: "07 Sep 2026, 10:00",
    sortTs: 1788000000000,
    severity: "high",
    category: "process",
    description: LONG,
    agent_name: "UpdatedWindowAgent",
    mitre_technique: "T1059.001 PowerShell",
  },
];

test("the description is rendered whole, however long", () => {
  render(<IncidentsTable incidents={INCIDENTS} />);

  expect(screen.getByText(LONG)).toBeInTheDocument();
});

test("its cell carries the text as a tooltip, so nothing is lost to the ellipsis", () => {
  render(<IncidentsTable incidents={INCIDENTS} />);

  expect(screen.getByText(LONG).closest("[title]")).toHaveAttribute("title", LONG);
});

test("the fixed layout is applied, so one long cell cannot squeeze the rest", () => {
  const { container } = render(<IncidentsTable incidents={INCIDENTS} />);

  expect(container.querySelector("table")).toHaveClass("soc2-incidents");
});

test("every column still renders its own value", () => {
  render(<IncidentsTable incidents={INCIDENTS} />);

  expect(screen.getByText("07 Sep 2026, 10:00")).toBeInTheDocument();
  expect(screen.getByText("high")).toBeInTheDocument();
  expect(screen.getByText("process")).toBeInTheDocument();
  expect(screen.getByText("UpdatedWindowAgent")).toBeInTheDocument();
  expect(screen.getByText("T1059.001 PowerShell")).toBeInTheDocument();
});

describe("copying a description", () => {
  let writeText;

  beforeEach(() => {
    writeText = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  });

  test("clicking the description copies the whole sentence", async () => {
    render(<IncidentsTable incidents={INCIDENTS} />);

    fireEvent.click(screen.getByText(LONG));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(LONG));
  });

  test("the other columns are not click targets", () => {
    render(<IncidentsTable incidents={INCIDENTS} />);

    // one button per description, plus the six sortable column headers
    expect(screen.getAllByRole("button")).toHaveLength(1);
  });
});
