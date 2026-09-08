/**
 * Clicking a path row in the SOC2 report puts that path on the clipboard.
 *
 * The values are real ones from /soc2-report/file: the labels the API returns
 * for top_directories / most_active_files, and a notable event whose message
 * wraps the path in a sentence. What matters in each case is that the click
 * lands anywhere on the row, and that what is copied is the path alone.
 */
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import { BreakdownList, DataTable } from "./SOC2Report";
import EventList from "./components/EventList";

const DIRECTORIES = [
  { label: "/var/log/sentinel-agent", count: 58760 },
  { label: "/var/log/oracle-cloud-agent/plugins/gomon", count: 22452 },
  { label: "C:\\ProgramData\\USOPrivate\\UpdateStore", count: 6957 },
];

let writeText;

beforeEach(() => {
  writeText = jest.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
});

describe("breakdown lists (Top directories / Most active files)", () => {
  test("clicking a row copies that row's label", async () => {
    render(<BreakdownList items={DIRECTORIES} copyable />);

    fireEvent.click(screen.getByText("/var/log/sentinel-agent"));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("/var/log/sentinel-agent"));
  });

  test("the count, not just the label, is part of the click target", async () => {
    render(<BreakdownList items={DIRECTORIES} copyable />);

    // 22,452 is the count cell of the gomon row — clicking it must copy that
    // row's path, which is the whole point of making the row the button
    fireEvent.click(screen.getByText("22,452"));

    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith("/var/log/oracle-cloud-agent/plugins/gomon")
    );
  });

  test("a Windows path survives verbatim", async () => {
    render(<BreakdownList items={DIRECTORIES} copyable />);

    fireEvent.click(screen.getByText("C:\\ProgramData\\USOPrivate\\UpdateStore"));

    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith("C:\\ProgramData\\USOPrivate\\UpdateStore")
    );
  });

  test("every row in the list is its own copy target", () => {
    render(<BreakdownList items={DIRECTORIES} copyable />);

    expect(screen.getAllByRole("button")).toHaveLength(DIRECTORIES.length);
  });

  test("a non-label list (severity, MITRE, users) copies its text just the same", async () => {
    render(
      <BreakdownList
        items={[{ label: "T1059.001 PowerShell", count: 12 }]}
        copyable
      />
    );

    fireEvent.click(screen.getByText("T1059.001 PowerShell"));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith("T1059.001 PowerShell"));
  });

  test("rows stay inert when copying is not switched on", () => {
    render(<BreakdownList items={DIRECTORIES} />);

    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });
});

describe("High-severity file changes", () => {
  const EVENTS = [
    {
      severity: "high",
      category: "file",
      message: "modified /var/log/sentinel-agent/agent.log (root)",
      copyValue: "/var/log/sentinel-agent/agent.log",
      timestamp: "03 Sep 2026, 10:00",
    },
  ];

  test("clicking the row copies the path, not the whole message", async () => {
    render(<EventList events={EVENTS} />);

    fireEvent.click(screen.getByText(/modified \/var\/log/));

    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith("/var/log/sentinel-agent/agent.log")
    );
  });

  test("the timestamp is part of the click target too", async () => {
    render(<EventList events={EVENTS} />);

    fireEvent.click(screen.getByText("03 Sep 2026, 10:00"));

    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith("/var/log/sentinel-agent/agent.log")
    );
  });

  test("an event with no path stays plain text", () => {
    render(<EventList events={[{ severity: "low", message: "service started", timestamp: "x" }]} />);

    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });
});

/**
 * Nothing is elided any more: the cell renders the whole value, and the
 * tooltip repeats it. A 64-character hash is the case that prompted this.
 */
describe("long values are shown whole", () => {
  const SHA = "f9d20897d41d71c3b0a3e2b4c5d6e7f8091a2b3c4d5e6f708192a3b4c5d6e7f8";
  const LONG = "/var/log/oracle-cloud-agent/plugins/unifiedmonitoring/unifiedmonitoring.log";

  const COLUMNS = [
    { key: "time", label: "Time (IST)" },
    { key: "path", label: "File" },
    { key: "sha", label: "SHA-256" },
    { key: "user", label: "User" },
  ];
  const ROWS = [{ time: "03 Sep 2026, 10:00", path: LONG, sha: SHA, user: "root" }];

  test("the SHA-256 cell renders all 64 characters", () => {
    render(<DataTable columns={COLUMNS} rows={ROWS} />);

    expect(screen.getByText(SHA)).toBeInTheDocument();
  });

  test("no cell ends in an ellipsis", () => {
    const { container } = render(<DataTable columns={COLUMNS} rows={ROWS} />);

    expect(container.textContent).not.toContain("…");
  });

  test("a hash is copyable, like the paths are", async () => {
    render(<DataTable columns={COLUMNS} rows={ROWS} />);

    fireEvent.click(screen.getByText(SHA));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(SHA));
  });

  test("the tooltip carries the same full value", () => {
    render(<DataTable columns={COLUMNS} rows={ROWS} />);

    expect(screen.getByText(LONG).closest("[title]")).toHaveAttribute("title", LONG);
  });
});
