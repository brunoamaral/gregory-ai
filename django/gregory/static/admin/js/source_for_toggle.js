'use strict';

(function () {
	var RULES = {
		'science paper': ['ctgov-settings', 'ctis-settings'],
		'news article':  ['ctgov-settings', 'ctis-settings', 'keyword-filter-settings'],
		'trials':        ['keyword-filter-settings'],
	};

	function setFieldsetDisabled(fieldset, disabled) {
		if (!fieldset) return;
		var inputs = fieldset.querySelectorAll('input, select, textarea');
		inputs.forEach(function (el) {
			if (disabled) {
				el.disabled = true;
				el.tabIndex = -1;
				el.setAttribute('aria-disabled', 'true');
			} else {
				el.disabled = false;
				el.removeAttribute('tabindex');
				el.removeAttribute('aria-disabled');
			}
		});
		fieldset.style.opacity = disabled ? '0.4' : '';
	}

	function applyState() {
		var sourceForSelect = document.getElementById('id_source_for');
		if (!sourceForSelect) return;

		var value = sourceForSelect.value;
		var toDisable = RULES[value] || [];

		// Reset all managed fieldsets first
		Object.keys(RULES).forEach(function (key) {
			RULES[key].forEach(function (cls) {
				var el = document.querySelector('fieldset.' + cls);
				setFieldsetDisabled(el, false);
			});
		});

		// Disable the ones for the current value
		toDisable.forEach(function (cls) {
			var el = document.querySelector('fieldset.' + cls);
			setFieldsetDisabled(el, true);
		});
	}

	function init() {
		var sourceForSelect = document.getElementById('id_source_for');
		if (!sourceForSelect) return;

		applyState();
		sourceForSelect.addEventListener('change', applyState);
	}

	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', init);
	} else {
		init();
	}
})();
