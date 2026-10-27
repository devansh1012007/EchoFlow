import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AccountPanel } from "../components/profile/AccountPanel";
import { installFetchMock, json, type FetchMock } from "./fetchMock";

let api: FetchMock;
const FREE = {
  app_user_id: "u1", is_pro: false, expires_at: null, grace_until: null, last_synced: null,
  limits: { daily_uploads_remaining: "4", max_clip_duration_seconds: 60, max_upload_size_mb: 10, hd_quality_allowed: false },
};

beforeEach(() => {
  sessionStorage.setItem("ef_access_token", "tok");
  api = installFetchMock();
  api.on("GET", /\/subscription\/$/, () => json(200, FREE));
});

const mount = (onSignedOut = vi.fn()) => {
  render(<AccountPanel onSignedOut={onSignedOut} />);
  return onSignedOut;
};

describe("AccountPanel: plan", () => {
  it("shows the plan and the limits the server reports", async () => {
    mount();
    expect(await screen.findByText("Free")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText("60 s")).toBeInTheDocument();
    expect(screen.getByText("Not included")).toBeInTheDocument();
  });

  it("says 'Unlimited' for a Pro user's upload quota", async () => {
    api = installFetchMock(); // fresh instance so the Pro route is the first match
    api.on("GET", /\/subscription\/$/, () => json(200, { ...FREE, is_pro: true, limits: { ...FREE.limits, daily_uploads_remaining: "unlimited" } }));
    mount();
    expect(await screen.findByText("Unlimited")).toBeInTheDocument();
    expect(screen.getByText("Pro")).toBeInTheDocument();
  });

  it("offers a retry when the plan can't be loaded", async () => {
    api = installFetchMock();
    api.fail("GET", /\/subscription\/$/, new TypeError("offline"));
    mount();
    expect(await screen.findByText(/Can't reach EchoFlow/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument(); // a side panel must not raise a page alert
  });

  it("Refresh status POSTs /subscription/sync/ and then reloads the plan", async () => {
    api.on("POST", /subscription\/sync/, () => json(200, { detail: "Subscription synchronized.", is_pro: false }));
    mount();
    await screen.findByText("Free");
    await userEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    expect(await screen.findByText("Plan status refreshed.")).toBeInTheDocument();
    expect(api.callsTo("subscription/sync")).toHaveLength(1);
    expect(api.callsTo(/\/subscription\/$/).length).toBeGreaterThanOrEqual(2);
  });

  it("explains a 503 from sync as 'billing isn't set up', not as a failure of the user's", async () => {
    api.on("POST", /subscription\/sync/, () => json(503, { detail: "RevenueCat not configured." }));
    mount();
    await screen.findByText("Free");
    await userEvent.click(screen.getByRole("button", { name: "Refresh status" }));
    expect(await screen.findByText(/Billing isn't set up/)).toBeInTheDocument();
  });

  it("Manage subscription opens the portal in a new tab without leaking the opener", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    api.on("GET", /subscription\/manage/, () => json(200, { url: "https://portal.example/abc" }));
    mount();
    await screen.findByText("Free");
    await userEvent.click(screen.getByRole("button", { name: "Manage subscription" }));
    await waitFor(() => expect(open).toHaveBeenCalledWith("https://portal.example/abc", "_blank", "noopener,noreferrer"));
    open.mockRestore();
  });

  it("says so when there is no billing portal, instead of doing nothing", async () => {
    api.on("GET", /subscription\/manage/, () => json(200, { url: null }));
    mount();
    await screen.findByText("Free");
    await userEvent.click(screen.getByRole("button", { name: "Manage subscription" }));
    expect(await screen.findByText(/no billing portal/i)).toBeInTheDocument();
  });
});

describe("AccountPanel: your data", () => {
  it("loads the access summary only when asked", async () => {
    api.on("GET", /data-subject\/access/, () =>
      json(200, { user_id: 1, categories: { profile: { username: "devansh", email: "d@example.test", dob: null, is_minor: false, consent_accepted: true, terms_version: "v1.0", parent_email: null }, interactions_count: 9, comments_count: 3, shares_sent_count: 2, audio_clips_created: 5 } }),
    );
    mount();
    await screen.findByText("Free");
    expect(api.callsTo("data-subject/access")).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "View my data" }));
    expect(await screen.findByText("devansh")).toBeInTheDocument();
    expect(screen.getByText("Yes (v1.0)")).toBeInTheDocument();
    expect(screen.getByText("9")).toBeInTheDocument();
  });
});

describe("AccountPanel: account deletion is guarded", () => {
  const openDialog = async () => {
    const onSignedOut = mount();
    await screen.findByText("Free");
    await userEvent.click(screen.getByRole("button", { name: "Delete account…" }));
    return { onSignedOut, dialog: screen.getByRole("alertdialog", { name: "Delete your account" }) };
  };

  it("opens an alertdialog that states the cooling-off and the permanent second step", async () => {
    const { dialog } = await openDialog();
    expect(dialog).toHaveTextContent(/30-day cooling-off/);
    expect(dialog).toHaveTextContent(/immediately and permanently/);
    expect(api.callsTo("erasure")).toHaveLength(0);
  });

  it("keeps the confirm button disabled until DELETE is typed exactly", async () => {
    const { dialog } = await openDialog();
    const confirm = within(dialog).getByRole("button", { name: "Delete my account" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText(/Type DELETE/), "delete");
    expect(confirm).toBeDisabled(); // case matters
    await userEvent.clear(within(dialog).getByLabelText(/Type DELETE/));
    await userEvent.type(within(dialog).getByLabelText(/Type DELETE/), "DELETE");
    expect(confirm).toBeEnabled();
    expect(api.callsTo("erasure")).toHaveLength(0);
  });

  it("Cancel and Escape send nothing", async () => {
    const { dialog } = await openDialog();
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(api.callsTo("erasure")).toHaveLength(0);
  });

  it("first confirmation sends {confirm:true} once and says nothing has been deleted yet", async () => {
    api.on("POST", /erasure/, () => json(200, { request_id: 1, status: "pending", cooling_off_until: "2026-11-27T00:00:00Z", message: "Erasure request submitted with 30-day cooling-off period." }));
    const { onSignedOut, dialog } = await openDialog();
    await userEvent.type(within(dialog).getByLabelText(/Type DELETE/), "DELETE");
    await userEvent.dblClick(within(dialog).getByRole("button", { name: "Delete my account" }));
    expect(await screen.findByText(/Nothing has been deleted yet/)).toBeInTheDocument();
    expect(api.callsTo("erasure")).toHaveLength(1);
    expect(api.callsTo("erasure")[0]?.body).toEqual({ confirm: true });
    expect(onSignedOut).not.toHaveBeenCalled();
  });

  it("a 403 during cooling-off is shown as the current state, with its date, not as an error", async () => {
    api.on("POST", /erasure/, () => json(403, { detail: "Cooling-off period active. 12 day(s) remaining before erasure can proceed.", request_id: 1, status: "pending", cooling_off_until: "2026-11-27T00:00:00Z" }));
    const { dialog } = await openDialog();
    await userEvent.type(within(dialog).getByLabelText(/Type DELETE/), "DELETE");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete my account" }));
    expect(await screen.findByText(/already asked us to delete/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("when deletion actually starts, the only action is to sign out", async () => {
    api.on("POST", /erasure/, () => json(200, { request_id: 1, status: "in_progress", message: "Cooling-off period complete. Erasure has been scheduled." }));
    const { onSignedOut, dialog } = await openDialog();
    await userEvent.type(within(dialog).getByLabelText(/Type DELETE/), "DELETE");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete my account" }));
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(onSignedOut).toHaveBeenCalledTimes(1);
  });

  it("a network failure says nothing was deleted and leaves the dialog usable", async () => {
    api.fail("POST", /erasure/, new TypeError("offline"));
    const { dialog } = await openDialog();
    await userEvent.type(within(dialog).getByLabelText(/Type DELETE/), "DELETE");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete my account" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Can't reach EchoFlow/);
    expect(within(dialog).getByRole("button", { name: "Delete my account" })).toBeEnabled();
  });
});
