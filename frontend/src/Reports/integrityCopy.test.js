/**
 * End to end for the column that was reported wrong: a real /soc2-report/file
 * payload goes through the real transform, the real table renders it, and the
 * click copies. Nothing is stubbed except the clipboard itself.
 */
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import { buildSoc2View } from "./soc2Transform";
import { DataTable } from "./SOC2Report";

const SHA = "d7f64f37c3fe84e3ac5d0eaa9746a3d601379a309f6dcc4f76364ad74fcfcd11";
const OLD_SHA = "2ec1af79cec83ee3ac5d0eaa9746a3d601379a309f6dcc4f76364ad74fcfcd11";
const PATH = "/var/log/oracle-cloud-agent/plugins/unifiedmonitoring/unifiedmonitoring.log";

let writeText;

beforeEach(() => {
  writeText = jest.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
});

function integrityTable() {
  const view = buildSoc2View(
    {
      file: {
        summary: { total_file_events: 1 },
        integrity_changes: [
          {
            timestamp: "2026-09-07T10:00:00",
            file_path: PATH,
            old_path: "/var/log/previous.log",
            sha256: SHA,
            old_sha256: OLD_SHA,
            user_name: "root",
          },
        ],
      },
    },
    {}
  );
  return view.views.file.tables.find((t) => /Integrity changes/.test(t.title));
}

test("the table the report builds carries the whole hash", () => {
  const row = integrityTable().rows[0];

  expect(row.sha).toBe(SHA);
  expect(row.oldSha).toBe(OLD_SHA);
  expect(JSON.stringify(row)).not.toContain("…");
});

test("all 64 characters are rendered on screen", () => {
  const t = integrityTable();
  render(<DataTable columns={t.columns} rows={t.rows} />);

  expect(screen.getByText(SHA)).toBeInTheDocument();
  expect(screen.getByText(OLD_SHA)).toBeInTheDocument();
});

test("clicking the SHA-256 cell copies all 64 characters", async () => {
  const t = integrityTable();
  render(<DataTable columns={t.columns} rows={t.rows} />);

  fireEvent.click(screen.getByText(SHA));

  await waitFor(() => expect(writeText).toHaveBeenCalledWith(SHA));
  expect(writeText.mock.calls[0][0]).toHaveLength(64);
});

test("clicking Previous SHA-256 copies that one, not the current one", async () => {
  const t = integrityTable();
  render(<DataTable columns={t.columns} rows={t.rows} />);

  fireEvent.click(screen.getByText(OLD_SHA));

  await waitFor(() => expect(writeText).toHaveBeenCalledWith(OLD_SHA));
});
