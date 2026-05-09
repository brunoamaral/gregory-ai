'use strict';

(function () {
	function applyState() {
		var sortSelect = document.getElementById('id_article_sort_order');
		var thresholdRow = document.getElementById('id_ml_threshold');
		if (!sortSelect || !thresholdRow) return;

		var isDateMode = sortSelect.value === 'date';

		// Walk up to the form-row wrapper so we can gray out the whole row
		var thresholdFormRow = thresholdRow.closest('.form-row') || thresholdRow.parentElement;

		if (isDateMode) {
			thresholdRow.disabled = true;
			thresholdRow.tabIndex = -1;
			thresholdRow.setAttribute('aria-disabled', 'true');
			if (thresholdFormRow) {
				thresholdFormRow.style.opacity = '0.4';
			}
		} else {
			thresholdRow.disabled = false;
			thresholdRow.removeAttribute('tabindex');
			thresholdRow.removeAttribute('aria-disabled');
			if (thresholdFormRow) {
				thresholdFormRow.style.opacity = '';
			}
		}
	}

	function init() {
		var sortSelect = document.getElementById('id_article_sort_order');
		if (!sortSelect) return;

		applyState();
		sortSelect.addEventListener('change', applyState);
	}

	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', init);
	} else {
		init();
	}
})();
