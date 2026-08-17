interface Props {
  onConfirm: () => void;
  onClose: () => void;
}

export default function UploadWarning({ onConfirm, onClose }: Props) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal upload-warning"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="upload-warning-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="upload-warning__title" id="upload-warning-title">
          Перед загрузкой файла
        </div>
        <div className="upload-warning__body">
          <p className="upload-warning__lead">
            Не загружайте конфиденциальные и личные документы, если у вас нет необходимых оснований
            для их обработки.
          </p>
          <div className="upload-warning__label">Не загружайте:</div>
          <ul className="upload-warning__list">
            <li>пароли</li>
            <li>банковские данные</li>
            <li>секретные ключи</li>
            <li>документы, содержащие особо чувствительную информацию</li>
            <li>чужие персональные документы без разрешения</li>
          </ul>
        </div>
        <div className="upload-warning__actions">
          <button type="button" className="btn btn--primary" onClick={onConfirm}>
            Хорошо
          </button>
        </div>
      </div>
    </div>
  );
}
