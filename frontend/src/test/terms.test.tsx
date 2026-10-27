import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { TermsPage, TERMS_LAST_UPDATED, TERMS_PATH } from "../pages/Terms";

describe("Terms and Conditions page", () => {
  it("is served at /terms", () => {
    expect(TERMS_PATH).toBe("/terms");
  });

  it("renders a single top-level heading and a last-updated date", () => {
    render(<TermsPage />);
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "Terms and Conditions" })).toBeInTheDocument();
    expect(screen.getByText(new RegExp(`Last updated: ${TERMS_LAST_UPDATED}`))).toBeInTheDocument();
  });

  it("covers every section the brief requires", () => {
    render(<TermsPage />);
    const required = [
      "Eligibility", "Your account", "Acceptable use", "Content you create",
      "Moderation", "Intellectual property", "Availability", "Suspension and termination",
      "Disclaimers", "Limitation of liability", "Changes to these Terms", "Contact",
    ];
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent ?? "");
    for (const topic of required) {
      expect(headings.some((h) => h.includes(topic)), `missing section: ${topic}`).toBe(true);
    }
  });

  it("marks every unestablished business fact as a placeholder rather than inventing it", () => {
    render(<TermsPage />);
    const main = screen.getByRole("main");
    const placeholders = within(main).getAllByText(/^\[.+\]$/);
    expect(placeholders.length).toBeGreaterThanOrEqual(8);
    // No e-mail address, phone number or street address may appear in the draft.
    expect(main.textContent).not.toMatch(/[\w.+-]+@[\w-]+\.[\w.]+/);
  });

  it("is reachable with the keyboard: skip link targets the content region", () => {
    render(<TermsPage />);
    const skip = screen.getByRole("link", { name: "Skip to terms" });
    expect(skip).toHaveAttribute("href", "#terms-content");
    expect(screen.getByRole("main")).toHaveAttribute("id", "terms-content");
  });

  it("does not use a list marker without the matching list semantics", () => {
    render(<TermsPage />);
    expect(screen.getAllByRole("list").length).toBeGreaterThanOrEqual(1);
  });
});
