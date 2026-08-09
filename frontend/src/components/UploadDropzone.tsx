import { useCallback, useRef, useState } from "react";
import type { DocumentOut } from "../types";
import { uploadDocument } from "../api";

interface UploadDropzoneProps {
  onUploaded: (doc: DocumentOut) => void;
  onError: (msg: string) => void;
}

export default function UploadDropzone({ onUploaded, onError }: UploadDropzoneProps) {
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const upload = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      const file = files[0];
      setBusy(true);
      try {
        const doc = await uploadDocument(file);
        onUploaded(doc);
      } catch (err) {
        onError(err instanceof Error ? err.message : "Upload failed");
      } finally {
        setBusy(false);
      }
    },
    [onUploaded, onError]
  );

  return (
    <div
      className={`dropzone ${dragging ? "dropzone--active" : ""} ${busy ? "dropzone--busy" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        void upload(e.dataTransfer.files);
      }}
      onClick={() => inputRef.current?.click()}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,.txt,.docx"
        hidden
        onChange={(e) => void upload(e.target.files)}
      />
      <div className="dropzone__icon">⬆</div>
      <div className="dropzone__title">{busy ? "Uploading..." : "Drag & drop a document here"}</div>
      <div className="dropzone__hint">PDF, TXT or DOCX — or click to browse</div>
    </div>
  );
}
