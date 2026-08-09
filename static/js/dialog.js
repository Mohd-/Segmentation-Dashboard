import { byId, esc } from './dom.js';
import { Store } from './state.js';
import { performLogin, fetchUserOptions } from './auth.js';

var inFlight = null;

function openDialog(options) {
  if (inFlight) return inFlight;
  var dialog = byId('app-dialog');
  var form = byId('app-dialog-form');
  var title = byId('app-dialog-title');
  var message = byId('app-dialog-message');
  var input = byId('app-dialog-input');
  var select = byId('app-dialog-select');
  var selectWrap = byId('app-dialog-select-label');
  var selectCaption = byId('app-dialog-select-caption');
  var cancelButton = byId('app-dialog-cancel');
  var confirmButton = byId('app-dialog-confirm');

  title.textContent = options.title || '';
  // The message is textContent (never markup); white-space: pre-line on the
  // element lets callers pass multi-line copy joined with '\n'.
  message.textContent = options.message || '';
  message.classList.toggle('hidden', !options.message);
  confirmButton.textContent = options.confirmLabel || 'OK';
  cancelButton.textContent = options.cancelLabel || 'Cancel';
  confirmButton.classList.toggle('danger', !!options.danger);
  input.classList.toggle('hidden', !options.isPrompt);
  if (options.isPrompt) input.value = options.initialValue || '';
  // Optional select, mirroring the prompt input: selectOptions entries are
  // either {value, label} objects or plain values; selectValue preselects.
  var hasSelect = !!(options.selectOptions && options.selectOptions.length);
  selectWrap.classList.toggle('hidden', !hasSelect);
  if (hasSelect) {
    selectCaption.textContent = options.selectLabel || '';
    select.innerHTML = options.selectOptions.map(function (option) {
      var value = (option && typeof option === 'object') ? option.value : option;
      var label = (option && typeof option === 'object') ? option.label : option;
      var selected = String(value) === String(options.selectValue) ? ' selected' : '';
      return '<option value="' + esc(value) + '"' + selected + '>' + esc(label) + '</option>';
    }).join('');
  }

  inFlight = new Promise(function (resolve) {
    function onClose() {
      dialog.removeEventListener('close', onClose);
      cancelButton.removeEventListener('click', onCancel);
      form.removeEventListener('submit', onSubmit);
      inFlight = null;
      if (dialog.returnValue === 'confirm') {
        // Prompt → trimmed input; select → selected option value; else true.
        resolve(options.isPrompt ? input.value.trim() : (hasSelect ? select.value : true));
      } else {
        resolve((options.isPrompt || hasSelect) ? null : false);
      }
    }
    function onCancel() { dialog.close('cancel'); }
    // `method="dialog"` otherwise closes with the submit button's value
    // (empty for our button), overwriting the intended confirmation result.
    // Closing explicitly keeps confirms, prompts, and select dialogs on one
    // deterministic return-value path.
    function onSubmit(event) {
      event.preventDefault();
      dialog.close('confirm');
    }

    dialog.returnValue = '';
    dialog.addEventListener('close', onClose);
    cancelButton.addEventListener('click', onCancel);
    form.addEventListener('submit', onSubmit);
    dialog.showModal();
    if (options.isPrompt) input.focus();
  });
  return inFlight;
}

// Plain confirm resolves true/false. With selectOptions it becomes a chooser:
// resolves the selected option's value (a string) on confirm, null on cancel.
export function confirmDialog(options) {
  var opts = options || {};
  return openDialog({
    title: opts.title,
    message: opts.message,
    confirmLabel: opts.confirmLabel || 'Confirm',
    cancelLabel: opts.cancelLabel || 'Cancel',
    danger: opts.danger,
    isPrompt: false,
    selectLabel: opts.selectLabel,
    selectOptions: opts.selectOptions,
    selectValue: opts.selectValue
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

// ---------------------------------------------------------------------------
// Login dialog (#login-dialog in index.html)
// ---------------------------------------------------------------------------
// Uses raw fetch (not api.js) on purpose: api.js opens THIS dialog on 401, so
// importing it here would create a require cycle and a login failure must
// never recursively re-open the dialog. /api/users and /api/login are both
// exempt from AUTH_REQUIRED, so these calls work without a session.

var loginInFlight = null;

export function loginDialog() {
  // All concurrent 401s share one dialog: every caller gets the same promise
  // and retries once the single sign-in completes.
  if (loginInFlight) return loginInFlight;
  var dialog = byId('login-dialog');
  if (!dialog) return Promise.resolve(null);
  var form = byId('login-form');
  var select = byId('login-name');
  var passcodeInput = byId('login-passcode');
  var errorEl = byId('login-error');

  errorEl.classList.add('hidden');
  passcodeInput.value = '';
  fetchUserOptions(select);

  loginInFlight = new Promise(function (resolve) {
    function finish(user) {
      dialog.removeEventListener('close', onClose);
      form.removeEventListener('submit', onSubmit);
      loginInFlight = null;
      resolve(user);
    }
    function onClose() { finish(null); } // Esc / dismissal: caller surfaces its original 401
    function onSubmit(event) {
      event.preventDefault();
      // performLogin sets Store.user + dispatches 'auth:changed' on success.
      performLogin(select.value, passcodeInput.value).then(function (result) {
        if (!result.ok) {
          errorEl.textContent = (result.body && result.body.detail) || 'Login failed.';
          errorEl.classList.remove('hidden');
          return;
        }
        dialog.removeEventListener('close', onClose); // our own close must not resolve null
        dialog.close();
        finish(Store.user);
      }).catch(function () {
        errorEl.textContent = 'Login failed. Check your connection and try again.';
        errorEl.classList.remove('hidden');
      });
    }
    dialog.addEventListener('close', onClose);
    form.addEventListener('submit', onSubmit);
    dialog.showModal();
  });
  return loginInFlight;
}
