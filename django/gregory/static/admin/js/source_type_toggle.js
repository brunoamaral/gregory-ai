'use strict';

// Sources add/change form (gregory/admin.py, SourceAdminForm):
// - shows only the connection settings the chosen "Source type" needs,
// - explains the chosen type in one line under the select,
// - limits the Subject list to the chosen team's subjects.
// Without JavaScript every row stays visible and the form's server-side checks
// still apply. Rows are hidden rather than disabled, so a saved value is never
// dropped just because its row is out of view.
(function () {
	function rowFor(name) {
		return document.querySelector('.form-row.field-' + name);
	}

	function parse(element, attribute, fallback) {
		try {
			return JSON.parse(element.getAttribute(attribute)) || fallback;
		} catch (error) {
			return fallback;
		}
	}

	function initSourceType() {
		var select = document.getElementById('id_source_type');
		if (!select) return;
		var rows = parse(select, 'data-rows', {});
		var help = parse(select, 'data-help', {});
		var connectionFields = parse(select, 'data-connection-fields', []);

		var note = document.createElement('div');
		note.className = 'help source-type-explanation';
		var row = rowFor('source_type');
		(row ? row.firstElementChild || row : select.parentNode).appendChild(note);

		function apply() {
			var value = select.value;
			// An existing source with a pair no importer fetches shows every setting,
			// so nothing it stores is out of view while someone fixes its type.
			var visible = value === 'legacy' ? connectionFields : rows[value] || [];
			connectionFields.forEach(function (name) {
				var fieldRow = rowFor(name);
				if (fieldRow) fieldRow.style.display = visible.indexOf(name) === -1 ? 'none' : '';
			});
			note.textContent = help[value] || 'Choose a source type to see the settings it needs.';
		}

		select.addEventListener('change', apply);
		apply();
	}

	function initSubjectFilter() {
		var team = document.getElementById('id_team');
		var subject = document.getElementById('id_subject');
		if (!team || !subject || !subject.hasAttribute('data-subject-teams')) return;
		var teamOf = parse(subject, 'data-subject-teams', {});
		var known = Array.prototype.slice.call(subject.options);

		function apply() {
			// Keep options added since the page loaded (the "+" popup) in the pool.
			Array.prototype.forEach.call(subject.options, function (option) {
				if (known.indexOf(option) === -1) known.push(option);
			});
			var chosen = subject.value;
			while (subject.options.length) subject.remove(0);
			known.forEach(function (option) {
				var owner = teamOf[option.value];
				if (!option.value || !team.value || owner === undefined || String(owner) === team.value) {
					subject.add(option);
				}
			});
			var stillThere = Array.prototype.some.call(subject.options, function (option) {
				return option.value === chosen;
			});
			subject.value = stillThere ? chosen : '';
		}

		team.addEventListener('change', apply);
		apply();
	}

	function init() {
		initSourceType();
		initSubjectFilter();
	}

	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', init);
	} else {
		init();
	}
})();
