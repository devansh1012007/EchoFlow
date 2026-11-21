import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { installFetchMock, json, type FetchMock } from "./fetchMock";
import { UploadPage } from "../pages/Upload";

/**
 * The Studio cover picker: the brief's "images uploaded by user" half, whose
 * backend (upload → validation → review gate) landed in patch 01. This file
 * pins the frontend half — the picker itself, the client-side checks the
 * backend also makes, and the review verdict surfaced after upload.
 */

let api: FetchMock;

function freeLimits() {
  return { limits: { daily_uploads_remaining: "5", max_clip_duration_seconds: "60", max_upload_size_mb: "10" } };
}

beforeEach(() => {
  api = installFetchMock();
  api.on("GET", /\/subscription\//, () => json(200, freeLimits()));
});

/** A minimal audio file so the form's own gates pass. */
function audioFile(): File {
  return new File([new Uint8Array(512)], "take.wav", { type: "audio/wav" });
}

function imageFile(name = "cover.png", type = "image/png", bytes = 1024): File {
  return new File([new Uint8Array(bytes)], name, { type });
}

async function fillForm(user: ReturnType<typeof userEvent.setup>) {
  const input = document.getElementById("upload-audio-file") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [audioFile()] } });
  await user.type(screen.getByRole("textbox", { name: /reel title/i }), "Cover me");
}

function pickCover(file: File) {
  const input = document.getElementById("upload-cover-file") as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

describe("the cover picker", () => {
  it("offers the picker and previews the chosen image", async () => {
    const user = userEvent.setup();
    render(<UploadPage onUploadSuccess={() => {}} />);
    await screen.findByRole("button", { name: /add a cover image/i });

    pickCover(imageFile());
    const preview = await waitFor(() => document.querySelector('img[alt=""]'));
    expect(preview).not.toBeNull();
    expect(screen.getByRole("button", { name: /remove cover/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /change cover/i })).toBeInTheDocument();
    expect(screen.getByText("cover.png")).toBeInTheDocument();
    void user;
  });

  it("refuses a non-image with the backend's own rule", async () => {
    render(<UploadPage onUploadSuccess={() => {}} />);
    await screen.findByRole("button", { name: /add a cover image/i });
    pickCover(imageFile("notes.txt", "text/plain"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/JPEG, PNG or WebP/i);
  });

  it("refuses an image over 5 MB before anything is sent", async () => {
    render(<UploadPage onUploadSuccess={() => {}} />);
    await screen.findByRole("button", { name: /add a cover image/i });
    pickCover(imageFile("huge.png", "image/png", 6 * 1024 * 1024));
    expect(await screen.findByRole("alert")).toHaveTextContent(/limit is 5 MB/i);
  });

  it("removing a cover clears the preview", async () => {
    const user = userEvent.setup();
    render(<UploadPage onUploadSuccess={() => {}} />);
    await screen.findByRole("button", { name: /add a cover image/i });
    pickCover(imageFile());
    await waitFor(() => document.querySelector('img[alt=""]'));
    await user.click(screen.getByRole("button", { name: /remove cover/i }));
    expect(document.querySelector('img[alt=""]')).toBeNull();
    expect(screen.getByRole("button", { name: /add a cover image/i })).toBeInTheDocument();
  });

  it("sends the cover with the upload and reports the review verdict", async () => {
    api.on("POST", /\/clips\//, () => json(202, { message: "Accepted", clip_id: "c-1", status: "processing", cover_status: "pending" }));
    api.on("POST", /approve-moderation/, () => json(200, { status: "approved" }));
    api.on("GET", /\/clips\/c-1\//, () => json(200, { id: "c-1", status: "ready" }));

    const user = userEvent.setup();
    render(<UploadPage onUploadSuccess={() => {}} />);
    await screen.findByRole("button", { name: /upload & launch reel/i });
    await fillForm(user);
    pickCover(imageFile());

    await user.click(screen.getByRole("button", { name: /upload & launch reel/i }));

    const uploadCall = api.callsTo(/clips\//)[0];
    expect((uploadCall?.body as FormData).get("cover_image")).toBeInstanceOf(File);
    await waitFor(() =>
      expect(screen.getByText(/held for review.*sound wave until a reviewer/i)).toBeInTheDocument(),
    );
  });

  it("uploads without a cover and makes no claim about covers", async () => {
    api.on("POST", /\/clips\//, () => json(202, { message: "Accepted", clip_id: "c-2", status: "processing" }));
    api.on("POST", /approve-moderation/, () => json(200, { status: "approved" }));
    api.on("GET", /\/clips\/c-2\//, () => json(200, { id: "c-2", status: "ready" }));

    const user = userEvent.setup();
    render(<UploadPage onUploadSuccess={() => {}} />);
    await screen.findByRole("button", { name: /upload & launch reel/i });
    await fillForm(user);

    await user.click(screen.getByRole("button", { name: /upload & launch reel/i }));

    const uploadCall = api.callsTo(/clips\//)[0];
    expect((uploadCall?.body as FormData).get("cover_image")).toBeNull();
    // The status poll runs on a 3 s cadence, so the wait must outlive it.
    await waitFor(() => expect(screen.getByText(/Transcode complete/i)).toBeInTheDocument(), { timeout: 8000 });
    expect(screen.queryByText(/cover/i)).toBeNull();
  });

  it("maps a server cover rejection to the form's error area", async () => {
    api.on("POST", /\/clips\//, () =>
      json(400, { cover_image: ["Image is too small. Both sides must be at least 200px."] }),
    );

    const user = userEvent.setup();
    render(<UploadPage onUploadSuccess={() => {}} />);
    await screen.findByRole("button", { name: /upload & launch reel/i });
    await fillForm(user);
    pickCover(imageFile());

    await user.click(screen.getByRole("button", { name: /upload & launch reel/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/Both sides must be at least 200px/),
    );
  });
});

describe("UploadsPanel cover review status", () => {
  it("labels a pending cover next to the processing state", async () => {
    vi.resetModules();
    const { UploadsPanel } = await import("../components/profile/UploadsPanel");
    const api2 = installFetchMock();
    api2.on("GET", /\/clips\/\?page=1/, () =>
      json(200, { count: 1, next: null, previous: null, results: [{ id: "u1", title: "Still cooking", status: "processing", cover_status: "pending" }] }),
    );
    render(<UploadsPanel />);
    await waitFor(() => expect(screen.getByText("Still cooking")).toBeInTheDocument());
    expect(screen.getByText(/cover in review/i)).toBeInTheDocument();
  });
});
