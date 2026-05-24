/**
 * CTA Button inserter for CKEditor 5 (django_ckeditor_5 integration).
 *
 * Appends a small inline form below each CKEditor 5 instance on the page.
 * The form has a Label field, a URL field, and an "Insert CTA Button" submit
 * button.  On submit, it inserts:
 *
 *   <a class="btn-cta" href="URL">Label</a>
 *
 * at the current editor cursor position.
 *
 * The server-side render pipeline (render_announcement_html) picks up this
 * element and wraps it in a bulletproof email <table> at send/preview time.
 *
 * Prerequisites:
 *   - CKEDITOR_5_CONFIGS['default']['htmlSupport']['allow'] must include
 *     { name: 'a', classes: ['btn-cta'] } so CKEditor preserves the class
 *     attribute in its model and HTML output.
 *   - This file must be listed in CKEDITOR_5_CONFIGS['default']['extraPlugins']
 *     (django_ckeditor_5 resolves the path via Django's staticfiles finder).
 *
 * Compatibility:
 *   Works with django-ckeditor-5 0.2.15 which exposes window.ClassicEditor
 *   and window.ckeditorRegisterCallback.
 *
 * No external dependencies; uses only browser built-ins and the CKEditor 5
 * data API (editor.data.processor / editor.data.toModel /
 * editor.model.insertContent).
 */

(function () {
	'use strict';

	/**
	 * Insert an <a class="btn-cta"> at the current editor selection.
	 *
	 * @param {object} editor  CKEditor 5 editor instance.
	 * @param {string} label   Button label (plain text; will be HTML-escaped by
	 *                         the data processor).
	 * @param {string} url     Button URL (must start with http:// or https://).
	 */
	function insertCtaButton(editor, label, url) {
		// Escape user input to avoid injecting HTML through the label or URL.
		// Both values appear inside HTML attribute or text content so all four
		// special characters must be escaped.
		function escapeHtml(str) {
			return String(str)
				.replace(/&/g, '&amp;')
				.replace(/</g, '&lt;')
				.replace(/>/g, '&gt;')
				.replace(/"/g, '&quot;');
		}

		var escapedLabel = escapeHtml(label);
		// URL goes into an href attribute; escape it so a crafted URL cannot
		// break out of the attribute and inject additional markup.
		var escapedUrl = escapeHtml(url);

		var html = '<a class="btn-cta" href="' + escapedUrl + '">' + escapedLabel + '</a>';

		// Convert the HTML string to a model fragment and insert it.
		// editor.data.processor.toView() uses CKEditor's data processor
		// (HtmlDataProcessor) to produce a view document fragment from the
		// HTML string.  editor.data.toModel() then converts that to a model
		// fragment honouring the current schema (including the GHS-allowed
		// class attribute).  editor.model.insertContent() inserts the
		// fragment at the current selection.
		try {
			var viewFragment = editor.data.processor.toView(html);
			var modelFragment = editor.data.toModel(viewFragment);
			editor.model.insertContent(modelFragment, editor.model.document.selection);
		} catch (err) {
			console.error('[button_plugin.js] Failed to insert CTA button:', err);
		}
	}

	/**
	 * Build and append the inserter UI below the CKEditor container element.
	 *
	 * @param {HTMLElement} editorEl  The textarea replaced by CKEditor.
	 * @param {object}      editor    CKEditor 5 editor instance.
	 */
	function buildUi(editorEl, editor) {
		var container = editorEl.closest('.ck-editor-container') || editorEl.parentElement;
		if (!container) return;

		// Guard against double-initialisation (e.g. Django inline formsets).
		if (container.querySelector('.btn-cta-inserter')) return;

		// Wrapper row
		var wrapper = document.createElement('div');
		wrapper.className = 'btn-cta-inserter';
		wrapper.style.cssText = [
			'display: flex',
			'flex-wrap: wrap',
			'gap: 8px',
			'align-items: center',
			'margin-top: 8px',
			'padding: 8px',
			'background: #f8fafc',
			'border: 1px solid #e2e8f0',
			'border-radius: 4px',
		].join('; ');

		// Section label
		var hint = document.createElement('span');
		hint.textContent = 'Insert CTA button:';
		hint.style.cssText = 'font-size: 12px; color: #64748b; white-space: nowrap;';

		// Label input
		var labelInput = document.createElement('input');
		labelInput.type = 'text';
		labelInput.placeholder = 'Button label';
		labelInput.setAttribute('aria-label', 'CTA button label');
		labelInput.style.cssText = [
			'padding: 4px 8px',
			'border: 1px solid #cbd5e1',
			'border-radius: 4px',
			'font-size: 13px',
			'flex: 1',
			'min-width: 120px',
		].join('; ');

		// URL input
		var urlInput = document.createElement('input');
		urlInput.type = 'url';
		urlInput.placeholder = 'https://example.com';
		urlInput.setAttribute('aria-label', 'CTA button URL');
		urlInput.style.cssText = [
			'padding: 4px 8px',
			'border: 1px solid #cbd5e1',
			'border-radius: 4px',
			'font-size: 13px',
			'flex: 2',
			'min-width: 180px',
		].join('; ');

		// Insert button
		var insertBtn = document.createElement('button');
		insertBtn.type = 'button';
		insertBtn.textContent = '+ Insert';
		insertBtn.style.cssText = [
			'padding: 4px 14px',
			'background: #1e3a8a',
			'color: #fff',
			'border: none',
			'border-radius: 4px',
			'font-size: 13px',
			'font-weight: 600',
			'cursor: pointer',
			'white-space: nowrap',
		].join('; ');

		insertBtn.addEventListener('click', function () {
			var label = labelInput.value.trim();
			var url = urlInput.value.trim();

			if (!label) {
				alert('Please enter a button label.');
				labelInput.focus();
				return;
			}
			if (!url) {
				alert('Please enter a URL.');
				urlInput.focus();
				return;
			}
			if (!/^https?:\/\//i.test(url)) {
				alert('URL must start with http:// or https://');
				urlInput.focus();
				return;
			}

			insertCtaButton(editor, label, url);

			// Clear and return focus to the editor
			labelInput.value = '';
			urlInput.value = '';
			editor.editing.view.focus();
		});

		// Allow Enter in URL field to submit
		urlInput.addEventListener('keydown', function (e) {
			if (e.key === 'Enter') {
				e.preventDefault();
				insertBtn.click();
			}
		});

		wrapper.appendChild(hint);
		wrapper.appendChild(labelInput);
		wrapper.appendChild(urlInput);
		wrapper.appendChild(insertBtn);
		container.appendChild(wrapper);
	}

	/**
	 * Register a setup callback for a single editor element.
	 *
	 * window.ckeditorRegisterCallback(id, fn) is provided by the
	 * django_ckeditor_5 bundle.  fn(editor) is called once the editor
	 * for element with the given id has been fully initialised.
	 *
	 * Timing: the bundle's DOMContentLoaded handler fires first and
	 * starts ClassicEditor.create() (a Promise).  This script's handler
	 * fires immediately after (synchronously, in the same event batch)
	 * and registers the callback before the Promise resolves, so we are
	 * always called at the right time.
	 *
	 * @param {HTMLElement} el  .django_ckeditor_5 textarea element.
	 */
	function setupForElement(el) {
		if (!el.id || !window.ckeditorRegisterCallback) return;
		window.ckeditorRegisterCallback(el.id, function (editor) {
			buildUi(el, editor);
		});
	}

	document.addEventListener('DOMContentLoaded', function () {
		document.querySelectorAll('.django_ckeditor_5').forEach(setupForElement);

		// Support Django admin inline formsets added dynamically.
		if (typeof django !== 'undefined' && django.jQuery) {
			django.jQuery(document).on('formset:added', function (event, row) {
				var el = row[0] || row;
				if (el && el.querySelectorAll) {
					el.querySelectorAll('.django_ckeditor_5').forEach(setupForElement);
				}
			});
		}
	});
})();
