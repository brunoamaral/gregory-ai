"""
rss.abstract_html: abstracts with JATS / PubMed HTML become readable, safe
feed HTML instead of escaped tags.
"""

from django.test import SimpleTestCase

from rss.abstract_html import clean_abstract_html, has_markup

# Trimmed from a real Crossref (SAGE) abstract, pretty-printing included.
JATS_ABSTRACT = """<jats:sec>
<jats:title>Background:</jats:title>
<jats:p>Racial and ethnic disparities are evident in multiple sclerosis.</jats:p>
</jats:sec>
<jats:sec>
<jats:title>Methods:</jats:title>
<jats:p>
                      self-reporting as non-Hispanic Black (
                      <jats:italic toggle="yes">n</jats:italic>
                       = 65), and CD19
                      <jats:sup>+</jats:sup>
                      B cells;
                      <jats:italic toggle="yes">p</jats:italic>
                       &lt; 0.001).
                    </jats:p>
</jats:sec>"""


class CleanAbstractHtmlTests(SimpleTestCase):
	def test_jats_sections_become_headings_and_paragraphs(self):
		html = clean_abstract_html(JATS_ABSTRACT)
		self.assertNotIn("jats", html)
		self.assertEqual(
			html,
			"<p><strong>Background:</strong></p>"
			"<p>Racial and ethnic disparities are evident in multiple sclerosis.</p>"
			"<p><strong>Methods:</strong></p>"
			"<p>self-reporting as non-Hispanic Black (<i>n</i> = 65), and "
			"CD19<sup>+</sup> B cells; <i>p</i> &lt; 0.001).</p>",
		)

	def test_leading_abstract_label_is_dropped(self):
		html = clean_abstract_html(
			"<jats:title>Abstract</jats:title><jats:p>Body text.</jats:p>"
		)
		self.assertEqual(html, "<p>Body text.</p>")

	def test_pubmed_html_keeps_paragraphs_and_drops_the_abstract_label(self):
		html = clean_abstract_html(
			'<div><p>J Neurol. 2023.</p><p><b>ABSTRACT</b></p>'
			'<p>BACKGROUND: text <a href="https://x.example">link</a>.</p></div>'
		)
		self.assertEqual(
			html, "<p>J Neurol. 2023.</p><p>BACKGROUND: text link.</p>"
		)

	def test_abstract_label_is_kept_when_asked(self):
		html = clean_abstract_html("<p>Abstract</p>", drop_abstract_label=False)
		self.assertEqual(html, "<p>Abstract</p>")

	def test_semantic_tags_keep_no_attributes(self):
		html = clean_abstract_html(
			'<p>CO<sub class="x">2</sub> and <jats:bold>bold</jats:bold> '
			'<em onclick="evil()">em</em></p>'
		)
		self.assertEqual(html, "<p>CO<sub>2</sub> and <b>bold</b> <em>em</em></p>")

	def test_scripts_styles_and_unknown_tags_cannot_inject_markup(self):
		html = clean_abstract_html(
			'<p>A <script>alert(1)</script><style>x{}</style>'
			'<img src=x onerror=alert(1)><span>&lt;b&gt;</span> B</p>'
		)
		self.assertNotIn("alert", html)
		self.assertNotIn("<img", html)
		self.assertNotIn("<span", html)
		self.assertIn("&lt;b&gt;", html)

	def test_mathml_keeps_text_and_tex_duplicate_is_dropped(self):
		html = clean_abstract_html(
			"<jats:p>TGF-<jats:inline-formula><jats:alternatives>"
			"<jats:tex-math>\\beta</jats:tex-math>"
			"<mml:math><mml:mi>β</mml:mi></mml:math>"
			"</jats:alternatives></jats:inline-formula></jats:p>"
		)
		self.assertEqual(html, "<p>TGF-β</p>")

	def test_br_lists_survive(self):
		html = clean_abstract_html("<p>EudraCT: 1<br />Sponsor: A &amp; B<br /></p>")
		self.assertEqual(html, "<p>EudraCT: 1<br>Sponsor: A &amp; B</p>")

	def test_plain_text_keeps_line_breaks_and_is_escaped(self):
		self.assertEqual(
			clean_abstract_html("Children aged <5 years\nsecond & line"),
			"Children aged &lt;5 years<br>second &amp; line",
		)

	def test_empty(self):
		self.assertEqual(clean_abstract_html(""), "")
		self.assertEqual(clean_abstract_html(None), "")

	def test_has_markup(self):
		self.assertTrue(has_markup("<jats:p>x</jats:p>"))
		self.assertTrue(has_markup("a<br />b"))
		self.assertFalse(has_markup("aged <5 years and >3"))
		self.assertFalse(has_markup(None))
