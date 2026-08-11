import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import UploadDropzone from "../UploadDropzone";

const TOKEN_KEY = "docsearch-token";

afterEach(() => {
  localStorage.clear();
  cleanup();
  vi.unstubAllGlobals();
});

describe("UploadDropzone authentication", () => {
  it("regression: upload after login sends the JWT as Authorization header", async () => {
    localStorage.setItem(TOKEN_KEY, "test.jwt.token");

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      url: "/api/documents/upload",
      json: async () => [
        {
          id: 42,
          filename: "somehash.txt",
          original_filename: "test.txt",
          file_type: "txt",
          file_size: 5,
          content_length: 5,
          created_at: "2026-01-01T00:00:00Z",
        },
      ],
    });
    vi.stubGlobal("fetch", fetchMock);

    const onUploaded = vi.fn();
    const onError = vi.fn();
    const { container } = render(<UploadDropzone onUploaded={onUploaded} onError={onError} />);

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    if (!input) throw new Error("file input not rendered");
    Object.defineProperty(input, "files", { value: [new File(["hello"], "test.txt", { type: "text/plain" })], configurable: true });
    fireEvent.change(input);

    await vi.waitFor(() => expect(onUploaded).toHaveBeenCalledTimes(1));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/documents/upload");
    expect(init.method).toBe("POST");
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer test.jwt.token");
    expect(headers.has("Authorization")).toBe(true);
    expect(onError).not.toHaveBeenCalled();
    expect(onUploaded).toHaveBeenCalledWith(expect.objectContaining({ id: 42 }));
  });

  it("regression: upload without token has no Authorization header but still calls endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      url: "/api/documents/upload",
      json: async () => [
        {
          id: 7,
          filename: "x.txt",
          original_filename: "plain.txt",
          file_type: "txt",
          file_size: 2,
          content_length: 2,
          created_at: "2026-01-01T00:00:00Z",
        },
      ],
    });
    vi.stubGlobal("fetch", fetchMock);

    const onUploaded = vi.fn();
    const { container } = render(<UploadDropzone onUploaded={onUploaded} onError={vi.fn()} />);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    Object.defineProperty(input, "files", { value: [new File(["hi"], "plain.txt", { type: "text/plain" })], configurable: true });
    fireEvent.change(input);

    await vi.waitFor(() => expect(onUploaded).toHaveBeenCalledTimes(1));

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers ?? {});
    expect(headers.get("Authorization")).toBeNull();
  });

  it("sends every selected file in a single upload request", async () => {
    localStorage.setItem(TOKEN_KEY, "test.jwt.token");

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      url: "/api/documents/upload",
      json: async () => [
        {
          id: 1,
          filename: "a.txt",
          original_filename: "a.txt",
          file_type: "txt",
          file_size: 1,
          content_length: 1,
          created_at: "2026-01-01T00:00:00Z",
        },
        {
          id: 2,
          filename: "b.txt",
          original_filename: "b.txt",
          file_type: "txt",
          file_size: 1,
          content_length: 1,
          created_at: "2026-01-01T00:00:00Z",
        },
      ],
    });
    vi.stubGlobal("fetch", fetchMock);

    const onUploaded = vi.fn();
    const { container } = render(<UploadDropzone onUploaded={onUploaded} onError={vi.fn()} />);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    Object.defineProperty(input, "files", {
      value: [
        new File(["a"], "a.txt", { type: "text/plain" }),
        new File(["b"], "b.txt", { type: "text/plain" }),
      ],
      configurable: true,
    });
    fireEvent.change(input);

    await vi.waitFor(() => expect(onUploaded).toHaveBeenCalledTimes(2));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = init.body as FormData;
    expect(body.getAll("file")).toHaveLength(2);
    expect(onUploaded).toHaveBeenNthCalledWith(1, expect.objectContaining({ id: 1 }));
    expect(onUploaded).toHaveBeenNthCalledWith(2, expect.objectContaining({ id: 2 }));
  });

  it("surfaces backend upload errors to the user", async () => {
    localStorage.setItem(TOKEN_KEY, "test.jwt.token");

    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      url: "/api/documents/upload",
      json: async () => ({ detail: "Too many files: 6. Maximum is 5 files per upload request." }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const onError = vi.fn();
    const onUploaded = vi.fn();
    const { container } = render(<UploadDropzone onUploaded={onUploaded} onError={onError} />);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    Object.defineProperty(input, "files", {
      value: [new File(["a"], "a.txt", { type: "text/plain" })],
      configurable: true,
    });
    fireEvent.change(input);

    await vi.waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
    expect(onError).toHaveBeenCalledWith(expect.stringContaining("Too many files"));
    expect(onUploaded).not.toHaveBeenCalled();
  });
});