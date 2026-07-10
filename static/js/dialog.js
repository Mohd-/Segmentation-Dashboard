import { byId } from './dom.js';

var inFlight = null;

function openDialog(options) {
  if (inFlight) return inFlight;
  var dialog = byId('app-dialog');
  var form = byId('app-dialog-form');
  var title = byId('app-dialog-title');
  var message = byId('app-dialog-message');
  var input = byId('app-dialog-input');
  var cancelButton = byId('app-dialog-cancel');
  var confirmButton = byId('app-dialog-confirm');

  title.textContent = options.title || '';
  message.textContent = options.message || '';
  message.classList.toggle('hidden', !options.message);
  confirmButton.textContent = options.confirmLabel || 'OK';
  cancelButton.textContent = options.cancelLabel || 'Cancel';
  confirmButton.classList.toggle('danger', !!options.danger);
  input.classList.toggle('hidden', !options.isPrompt);
  if (options.isPrompt) input.value = options.initialValue || '';

  inFlight = new Promise(function (resolve) {
    function onClose() {
      dialog.removeEventListener('close', onClose);
      cancelButton.removeEventListener('click', onCancel);
      form.removeEventListener('submit', onSubmit);
      inFlight = null;
      if (dialog.returnValue === 'confirm') {
        resolve(options.isPrompt ? input.value.trim() : true);
      } else {
        resolve(options.isPrompt ? null : false);
      }
    }
    function onCancel() { dialog.close('cancel'); }
    function onSubmit() { dialog.returnValue = 'confirm'; }

    dialog.returnValue = '';
    dialog.addEventListener('close', onClose);
    cancelButton.addEventListener('click', onCancel);
    form.addEventListener('submit', onSubmit);
    dialog.showModal();
    if (options.isPrompt) input.focus();
  });
  return inFlight;
}

export function confirmDialog(options) {
  var opts = options || {};
  return openDialog({
    title: opts.title,
    message: opts.message,
    confirmLabel: opts.confirmLabel || 'Confirm',
    cancelLabel: opts.cancelLabel || 'Cancel',
    danger: opts.danger,
    isPrompt: false
  });
}

export function promptDialog(options) {
  var opts = options || {};
  return openDialog({
    title: opts.title,
    message: opts.message,
    confirmLabel: opts.confirmLabel || 'Save',
    cancelLabel: 'Cancel',
    danger: false,
    isPrompt: true,
    initialValue: opts.initialValue || ''
  });
}
