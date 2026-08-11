import { useCallback, useRef, useState } from "react";
import type { DocumentOut } from "../types";
import { uploadDocuments } from "../api";

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
      const selected = Array.from(files);
      setBusy(true);
      try {
        const docs = await uploadDocuments(selected);
        docs.forEach((doc) => onUploaded(doc));
      } catch (err) {
        onError(err instanceof Error ? err.message : "Не удалось загрузить файл");
      } finally {
        setBusy(false);
      }
    },
    [onUploaded, onError]
  );

  return (
    <div
      className={`dropzone ${dragging ? "dropzone--active" : ""} ${busy ? "dropzone--uploading" : ""}`}
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
        accept=".pdf,.txt,.docx,.md,.odt"
        multiple
        hidden
        onChange={(e) => {
          void upload(e.target.files);
          e.target.value = "";
        }}
      />
      <div className="dropzone__icon">⬆</div>
      <div className="dropzone__title">
        {busy ? "Загрузка…" : "Перетащите документы сюда"}
      </div>
      <div className="dropzone__hint">
        PDF, TXT, DOCX, Markdown или ODT — несколько файлов за раз или нажмите для выбора
      </div>
    </div>
  );
}
