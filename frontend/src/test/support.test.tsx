import { beforeEach, describe, expect, it } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SupportPage, extractClipId } from "../pages/Support";
import { resolvePublicRoute } from "../lib/publicRoutes";
import { installFetchMock, json, type FetchMock } from "./fetchMock";

let api: FetchMock;
const CLIP = "33333333-3333-4333-8333-333333333333";
const COMPLIANCE = {
  compliance_officer: { name: "Not configured", email: "" },
  grievance_officer: { name: "A. Officer", email: "grievance@example.test" },
  nodal_contact: { name: "Not configured", email: "" },
  terms_versions: ["v1.0"], current_terms_version: "v1.0", privacy_version: "v1.0", physical_address: "",
};

beforeEach(() => {
  api = installFetchMock();
  api.on("GET", /legal\/compliance/, () => json(200, COMPLIANCE));
});

describe("public routes", () => {
  it.each(["/terms", "/terms/", "/support"])("%s resolves to a public page", (p) => {
    expect(resolvePublicRoute(p)).not.toBeNull();
  });
  it.each(["/", "/profile", "/termsx", "/clip"])("%s falls through to the app", (p) => {
    expect(resolvePublicRoute(p)).toBeNull();
  });
});

describe("extractClipId", () => {
  it("accepts a bare UUID, a share link, and mixed case", () => {
    expect(extractClipId(CLIP)).toBe(CLIP);
    expect(extractClipId(`https://echoflow.example/clip/${CLIP}?s=abc`)).toBe(CLIP);
    expect(extractClipId(` ${CLIP.toUpperCase()} `)).toBe(CLIP);
  });
  it("rejects anything without a UUID", () => {
    expect(extractClipId("not a clip")).toBeNull();
    expect(extractClipId("")).toBeNull();
  });
});

describe("Support page: contacts", () => {
  it("shows a published officer's email and says 'Not yet published' for the rest", async () => {
    render(<SupportPage />);
    expect(await screen.findByRole("link", { name: "grievance@example.test" })).toHaveAttribute("href", "mailto:grievance@example.test");
    expect(screen.getAllByText("Not yet published")).toHaveLength(2);
  });

  it("still offers both forms when contact details fail to load", async () => {
    api = installFetchMock();
    api.fail("GET", /legal\/compliance/, new TypeError("offline"));
    render(<SupportPage />);
    expect(await screen.findByText(/couldn't be loaded/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit grievance" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send takedown request" })).toBeInTheDocument();
  });
});

describe("Support page: grievance", () => {
  it("requires a subject and a description before it calls the server", async () => {
    render(<SupportPage />);
    await userEvent.click(screen.getByRole("button", { name: "Submit grievance" }));
    expect(await screen.findByText("Add a subject.")).toBeInTheDocument();
    expect(api.callsTo("/grievance/")).toHaveLength(0);
  });

  it("rejects a malformed email before it reaches the backend", async () => {
    render(<SupportPage />);
    const form = screen.getByRole("button", { name: "Submit grievance" }).closest("form") as HTMLElement;
    await userEvent.type(within(form).getByLabelText("Subject"), "s");
    await userEvent.type(within(form).getByLabelText(/What happened/), "d");
    await userEvent.type(within(form).getByLabelText(/Your email/), "nope");
    await userEvent.click(within(form).getByRole("button", { name: "Submit grievance" }));
    expect(await screen.findByText(/doesn't look right/)).toBeInTheDocument();
    expect(api.callsTo("/grievance/")).toHaveLength(0);
  });

  it("submits once and shows the reference number", async () => {
    api.on("POST", /\/grievance\//, () => json(201, { grievance_id: 42, status: "received", message: "Grievance received. Acknowledgment within 24 hours." }));
    render(<SupportPage />);
    const form = screen.getByRole("button", { name: "Submit grievance" }).closest("form") as HTMLElement;
    await userEvent.type(within(form).getByLabelText("Subject"), "Broken upload");
    await userEvent.type(within(form).getByLabelText(/What happened/), "It hangs at 90%");
    await userEvent.click(within(form).getByRole("button", { name: "Submit grievance" }));
    expect(await screen.findByText(/Reference: 42/)).toBeInTheDocument();
    const calls = api.callsTo("/grievance/");
    expect(calls).toHaveLength(1);
    expect(calls[0]?.body).toEqual({ subject: "Broken upload", description: "It hangs at 90%" });
  });

  it("explains a server rejection and keeps what was typed", async () => {
    api.on("POST", /\/grievance\//, () => json(400, { detail: "Subject is too long." }));
    render(<SupportPage />);
    const form = screen.getByRole("button", { name: "Submit grievance" }).closest("form") as HTMLElement;
    await userEvent.type(within(form).getByLabelText("Subject"), "s");
    await userEvent.type(within(form).getByLabelText(/What happened/), "d");
    await userEvent.click(within(form).getByRole("button", { name: "Submit grievance" }));
    expect(await screen.findByText("Subject is too long.")).toBeInTheDocument();
    expect(within(form).getByLabelText("Subject")).toHaveValue("s");
  });
});

describe("Support page: takedown", () => {
  const fill = async (clip: string) => {
    render(<SupportPage />);
    const form = screen.getByRole("button", { name: "Send takedown request" }).closest("form") as HTMLElement;
    await userEvent.type(within(form).getByLabelText("Clip link or ID"), clip);
    await userEvent.type(within(form).getByLabelText(/Reason for removal/), "It is my recording");
    await userEvent.click(within(form).getByRole("button", { name: "Send takedown request" }));
    return form;
  };

  it("pulls the UUID out of a pasted share link and sends it", async () => {
    api.on("POST", /legal\/takedown/, () => json(201, { message: "ok" }));
    await fill(`https://echoflow.example/clip/${CLIP}?s=tok`);
    await screen.findByText(/Takedown request received/);
    expect(api.callsTo("takedown")[0]?.body).toEqual({ clip_id: CLIP, reason: "It is my recording" });
  });

  it("refuses text that holds no clip ID", async () => {
    await fill("my favourite song");
    expect(await screen.findByText(/Paste the clip's link or its ID/)).toBeInTheDocument();
    expect(api.callsTo("takedown")).toHaveLength(0);
  });

  it("turns a 404 into a plain 'couldn't find that clip'", async () => {
    api.on("POST", /legal\/takedown/, () => json(404, { error: "Clip not found." }));
    await fill(CLIP);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/couldn't find that clip/i));
  });
});
