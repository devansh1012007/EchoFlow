import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReportDialog } from "../components/feed/ReportDialog";
import { installFetchMock, json, type FetchMock } from "./fetchMock";

let api: FetchMock;
const ID = "22222222-2222-4222-8222-222222222222";

beforeEach(() => {
  sessionStorage.setItem("ef_access_token", "tok");
  api = installFetchMock();
});

const open = (onClose = vi.fn()) => {
  render(<ReportDialog clipId={ID} clipTitle="Rain on a tin roof" onClose={onClose} />);
  return onClose;
};

describe("ReportDialog", () => {
  it("is a labelled modal dialog and moves focus into itself", () => {
    open();
    const dialog = screen.getByRole("dialog", { name: "Report this clip" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveFocus();
  });

  it("will not send without a reason, and says so", async () => {
    open();
    await userEvent.click(screen.getByRole("button", { name: "Send report" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a reason");
    expect(api.callsTo("/report/")).toHaveLength(0);
  });

  it("will not send without a note (the backend rejects empty content)", async () => {
    open();
    await userEvent.selectOptions(screen.getByLabelText("Reason"), "spam");
    await userEvent.click(screen.getByRole("button", { name: "Send report" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/short note/);
    expect(api.callsTo("/report/")).toHaveLength(0);
  });

  it("sends exactly the chosen reason and trimmed note, once, then confirms", async () => {
    api.on("POST", /\/report\//, () => json(201, { detail: "ok" }));
    open();
    await userEvent.selectOptions(screen.getByLabelText("Reason"), "copyright");
    await userEvent.type(screen.getByLabelText(/What should we know/), "  stolen from my channel ");
    const send = screen.getByRole("button", { name: "Send report" });
    await userEvent.dblClick(send); // a double-click must not file two reports
    expect(await screen.findByText(/Report received/)).toBeInTheDocument();
    const calls = api.callsTo(`/clips/${ID}/report/`);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.body).toEqual({ report_reason: "copyright", content: "stolen from my channel" });
  });

  it("shows a rate-limit message with the wait, and keeps the form so nothing typed is lost", async () => {
    api.on("POST", /\/report\//, () => json(429, { detail: "throttled" }, { "Retry-After": "30" }));
    open();
    await userEvent.selectOptions(screen.getByLabelText("Reason"), "spam");
    await userEvent.type(screen.getByLabelText(/What should we know/), "ad");
    await userEvent.click(screen.getByRole("button", { name: "Send report" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("30 seconds");
    expect(screen.getByLabelText(/What should we know/)).toHaveValue("ad");
    expect(screen.getByRole("button", { name: "Send report" })).toBeEnabled();
  });

  it("reports a dead connection as a connection problem, not a validation one", async () => {
    api.fail("POST", /\/report\//, new TypeError("Failed to fetch"));
    open();
    await userEvent.selectOptions(screen.getByLabelText("Reason"), "other");
    await userEvent.type(screen.getByLabelText(/What should we know/), "x");
    await userEvent.click(screen.getByRole("button", { name: "Send report" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Can't reach EchoFlow/);
  });

  it("Escape closes it", async () => {
    const onClose = open();
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("offers every reason the backend accepts", () => {
    open();
    const values = [...screen.getByLabelText("Reason").querySelectorAll("option")].map((o) => o.getAttribute("value")).filter(Boolean);
    expect(values).toEqual(["obscene", "hate_speech", "violence", "csam", "terrorism", "copyright", "impersonation", "privacy", "spam", "other"]);
  });
});
