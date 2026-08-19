import { useCallback, useRef, useState } from "react";
import type { DocumentOut } from "../types";
import { uploadDocuments } from "../api";
import { hasSeenUploadWarning, markUploadWarningSeen } from "../consent";
import { useI18n } from "../i18n";
import UploadWarning from "./UploadWarning";

interface UploadDropzoneProps {
  onUploaded: (doc: DocumentOut) => void;
  onError: (msg: string) => void;
}

export default function UploadDropzone({ onUploaded, onError }: UploadDropzoneProps) {
  const { t } = useI18n();
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [warningPending, setWarningPending] = useState<File[] | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const doUpload = useCallback(
    async (files: File[]) => {
      setBusy(true);
      try {
        const docs = await uploadDocuments(files);
        docs.forEach((doc) => onUploaded(doc));
      } catch (err) {
        onError(err instanceof Error ? err.message : t("dropzone.error"));
      } finally {
        setBusy(false);
      }
    },
    [onUploaded, onError, t]
  );

  const upload = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      const selected = Array.from(files);
      if (hasSeenUploadWarning()) {
        await doUpload(selected);
      } else {
        setWarningPending(selected);
      }
    },
    [doUpload]
  );

  const confirmWarning = useCallback(() => {
    markUploadWarningSeen();
    const pending = warningPending;
    setWarningPending(null);
    if (pending) void doUpload(pending);
  }, [warningPending, doUpload]);

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
        {busy ? (
          t("dropzone.uploading")
        ) : (
          <>
            <span className="dropzone__label-desktop">{t("dropzone.drag")}</span>
            <span className="dropzone__label-mobile">{t("dropzone.upload")}</span>
          </>
        )}
      </div>
      <div className="dropzone__hint">{t("dropzone.hint")}</div>

      {warningPending && (
        <UploadWarning
          onConfirm={confirmWarning}
          onClose={() => setWarningPending(null)}
        />
      )}
    </div>
  );
}
